"""Report figures, one file per figure, drawn from the stage 3-5 outputs.

    python -m reclaim.figures

Each figure is written to outputs/figures/ as PDF and SVG (editable text) and a 300 dpi PNG.
Sizes are in millimetres for an A4 report with a 170 mm text width. Figures carry no titles;
titles and captions belong in the report.
"""
import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.lines import Line2D
from pyproj import Transformer

from . import config, detect
from .classify import MASKS
from .config import OUTPUTS, ROOT

FIG = OUTPUTS / "figures"
TABLES = OUTPUTS / "tables"
MM = 1 / 25.4

INK, INK2, MUTED, LAND, GRID = "#0b0b0b", "#52514e", "#898781", "#e4e3de", "#e8e7e2"
BLUE, ORANGE, GREY = "#2a78d6", "#eb6834", "#b9b8b2"
RAMP = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
        "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
# warm sequential ramp for the year map: ColorBrewer YlOrRd from 0.3 to 1.0, so the earliest
# year still stands out from the white sea
WARM = ["#feca66", "#feb24c", "#fd9a42", "#fd7c37", "#fc552c",
        "#ef3323", "#dd161d", "#c50624", "#a40026", "#800026"]
PERIODS = {"map_2010_11": ("2016-2018, 2010/11 habitat map", BLUE, "o"),
           "map_2018": ("2019-2025, 2018 habitat map", ORANGE, "s")}

mpl.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 7.5, "axes.labelsize": 7.5, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "legend.fontsize": 7, "svg.fonttype": "none", "pdf.fonttype": 42,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
    "text.color": INK, "axes.labelcolor": INK2, "axes.edgecolor": MUTED,
    "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
})


def table(name):
    return pd.read_csv(TABLES / f"{name}.csv")


# ---------------------------------------------------------------- maps

class Basemap:
    """2015 land, the Singapore boundary and place names on the stage 2 grid."""
    PLACES = {  # label: (lon, lat, ha)
        "Tuas": (103.648, 1.318, "center"),
        "Jurong Island": (103.690, 1.236, "center"),
        "Semakau": (103.788, 1.193, "left"),
        "Changi": (103.965, 1.356, "center"),
        "Pulau\nTekong": (104.064, 1.404, "center"),
    }
    REGIONS = {"MALAYSIA": (103.66, 1.468), "INDONESIA": (104.03, 1.150)}

    def __init__(self, cfg):
        with rasterio.open(MASKS / "mask_2015.tif") as src:
            self.land = src.read(1) == 0
            self.bounds = src.bounds
        self.crs = cfg["project"]["crs"]
        self.boundary = gpd.read_file(ROOT / cfg["aoi"]["boundary_file"]).to_crs(self.crs)
        self.to_xy = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True).transform

    def figure(self):
        """170 mm wide figure with an equal-aspect map axes and a slot for a colour bar."""
        b = self.bounds
        w = 146 * MM
        h = w * (b.top - b.bottom) / (b.right - b.left)
        fig = plt.figure(figsize=(170 * MM, h + 4 * MM))
        ax = fig.add_axes([2 * MM / fig.get_figwidth(), 2 * MM / fig.get_figheight(),
                           w / fig.get_figwidth(), h / fig.get_figheight()])
        cax = fig.add_axes([152 * MM / fig.get_figwidth(), 0.25, 3 * MM / fig.get_figwidth(), 0.5])
        return fig, ax, cax

    def draw(self, ax):
        b = self.bounds
        ax.imshow(np.ma.masked_equal(self.land, False), cmap=ListedColormap([LAND]),
                  extent=(b.left, b.right, b.bottom, b.top), interpolation="nearest", rasterized=True)
        self.boundary.boundary.plot(ax=ax, color=MUTED, lw=0.5, ls="--")
        ax.set_xlim(b.left, b.right)
        ax.set_ylim(b.bottom, b.top)
        ax.set_axis_off()

    def annotate(self, ax):
        for name, (lon, lat, ha) in self.PLACES.items():
            ax.text(*self.to_xy(lon, lat), name, ha=ha, va="center", fontsize=7, color=INK)
        for name, (lon, lat) in self.REGIONS.items():
            ax.text(*self.to_xy(lon, lat), name, ha="center", va="center", fontsize=6.5,
                    color=MUTED, style="italic")
        # 5 km scale bar and north arrow in the open sea at the lower left
        b = self.bounds
        x0, y0 = b.left + 2500, b.bottom + 2200
        ax.plot([x0, x0 + 5000], [y0, y0], color=INK, lw=1.2, solid_capstyle="butt")
        ax.text(x0 + 2500, y0 + 500, "5 km", ha="center", va="bottom", fontsize=7)
        ax.annotate("N", xy=(x0 + 500, y0 + 6500), xytext=(x0 + 500, y0 + 3000), ha="center",
                    va="center", fontsize=7, arrowprops=dict(arrowstyle="-|>", color=INK, lw=0.8))


def fig_map(cfg):
    """Reclaimed land coloured by the year it was first detected."""
    base = Basemap(cfg)
    rec = gpd.read_file(detect.OUT)
    fig, ax, cax = base.figure()
    base.draw(ax)
    years = list(range(2016, 2026))
    cmap, norm = ListedColormap(WARM), BoundaryNorm(np.arange(2015.5, 2026), len(WARM))
    rec.plot(ax=ax, column="year", cmap=cmap, norm=norm, lw=0)
    base.annotate(ax)
    cb = fig.colorbar(mpl.cm.ScalarMappable(norm, cmap), cax=cax, ticks=years)
    cb.set_label("Year first detected as land")
    cb.outline.set_visible(False)
    cb.ax.tick_params(length=0)
    return fig


def fig_hotspots(cfg):
    """Reclaimed hectares per 1 km cell; polygons are cut by the cells, so no cell exceeds 100 ha."""
    from shapely.geometry import box
    base = Basemap(cfg)
    rec = gpd.read_file(detect.OUT)
    cell = 1000
    x0, y0, x1, y1 = (np.asarray(rec.total_bounds) // cell * cell) + [0, 0, cell, cell]
    cells = gpd.GeoDataFrame(geometry=[box(x, y, x + cell, y + cell) for x in np.arange(x0, x1, cell)
                                       for y in np.arange(y0, y1, cell)], crs=rec.crs).reset_index(names="cell")
    parts = gpd.overlay(rec, cells, how="intersection", keep_geom_type=True)
    ha = (parts.area / 1e4).groupby(parts.cell).sum()
    grid = cells.set_index("cell").join(ha.rename("ha"), how="inner")
    fig, ax, cax = base.figure()
    base.draw(ax)
    bins = [0, 5, 10, 20, 40, 80]
    cmap, norm = ListedColormap([RAMP[i] for i in (0, 2, 4, 6, 9)]), BoundaryNorm(bins, 5)
    grid.plot(ax=ax, column="ha", cmap=cmap, norm=norm, lw=0)
    base.annotate(ax)
    cb = fig.colorbar(mpl.cm.ScalarMappable(norm, cmap), cax=cax, ticks=bins)
    cb.set_label("Reclaimed area per 1 km cell (ha)")
    cb.outline.set_visible(False)
    cb.ax.tick_params(length=0)
    return fig


# ---------------------------------------------------------------- charts

def fig_annual(cfg):
    """Detected hectares per year against SLA's annual increase in total land area."""
    t = table("05_rq1_annual")
    fig, ax = plt.subplots(figsize=(170 * MM, 62 * MM), layout="constrained")
    x = np.arange(len(t))
    ax.bar(x - 0.2, t.detected_ha, 0.38, color=BLUE, edgecolor="white", lw=0.8,
           label=f"Detected from Sentinel-2 (total {t.detected_ha.sum():,.0f} ha)")
    ax.bar(x + 0.2, t.sla_increase_ha, 0.38, color=GREY, edgecolor="white", lw=0.8,
           label=f"SLA annual increase in land area (total {t.sla_increase_ha.sum():,.0f} ha)")
    ax.set_xticks(x, t.year)
    ax.set_ylabel("Area (ha)")
    ax.yaxis.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", length=0)
    ax.legend(loc="upper left", ncols=2)
    ax.set_ylim(0, t[["detected_ha", "sla_increase_ha"]].to_numpy().max() * 1.15)
    return fig


def fig_composition(cfg):
    """How the reclaimed area splits into mapped habitat, Tekong reclamation works and unmapped water."""
    g = table("05_rq2_habitat").groupby("group").area_ha.sum()
    parts = [("habitat", "Mapped habitat", BLUE),
             ("works", cfg["habitat"]["works"]["label"], ORANGE),
             ("unmapped", "Unmapped / open water", GREY)]
    fig, ax = plt.subplots(figsize=(170 * MM, 24 * MM), layout="constrained")
    left = 0
    for key, label, color in parts:
        v = g[key]
        ax.barh(0, v, left=left, height=0.6, color=color, edgecolor="white", lw=1,
                label=f"{label}: {v:,.0f} ha ({v / g.sum() * 100:.0f}%)")
        left += v
    ax.set_xlim(0, g.sum())
    ax.set_ylim(-0.4, 0.4)
    ax.set_axis_off()
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.05), ncols=3, handlelength=1.2)
    return fig


def fig_habitat(cfg):
    """Mapped habitat under the reclaimed land, by class and baseline period."""
    t = table("05_rq2_habitat")
    typ = cfg["habitat"]["type_field"]
    hab = t[t.group == "habitat"].pivot_table(index=typ, columns="baseline", values="area_ha",
                                              aggfunc="sum", fill_value=0)
    hab = hab.loc[hab.sum(axis=1).sort_values().index]
    fig, ax = plt.subplots(figsize=(170 * MM, 92 * MM), layout="constrained")
    y = np.arange(len(hab))
    left = np.zeros(len(hab))
    for key, (label, color, _) in PERIODS.items():
        ax.barh(y, hab[key], left=left, height=0.7, color=color, edgecolor="white", lw=0.6, label=label)
        left += hab[key].to_numpy()
    ax.set_yticks(y, hab.index)
    ax.tick_params(axis="y", length=0)
    ax.set_xlabel("Reclaimed area (ha)")
    ax.xaxis.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1), ncols=2)
    return fig


def fig_selection(cfg):
    """Selection ratio with 95% bootstrap intervals; filled markers where the interval excludes 1."""
    t = table("05_rq3_selection")
    typ = cfg["habitat"]["type_field"]
    t = t[t.reclaimed_ha > 0]
    order = t.groupby(typ).selection_ratio.max().sort_values().index
    xmin, xmax = 0.02, 40
    fig, ax = plt.subplots(figsize=(170 * MM, 100 * MM), layout="constrained")
    ax.axvline(1, color=MUTED, lw=0.8, ls=(0, (4, 2)), zorder=1)
    pos = {c: i for i, c in enumerate(order)}
    for (key, (label, color, marker)), dy in zip(PERIODS.items(), (0.18, -0.18)):
        d = t[t.baseline == key]
        y = d[typ].map(pos).to_numpy() + dy
        lo = d.ci_low.clip(lower=xmin).to_numpy()
        ax.hlines(y, lo, d.ci_high, color=color, lw=1.1, zorder=2)
        cut = d.ci_low.to_numpy() < xmin
        ax.scatter(np.full(cut.sum(), xmin), y[cut], marker="<", s=12, color=color, lw=0, zorder=3)
        sig = ((d.ci_low > 1) | (d.ci_high < 1)).to_numpy()
        ax.scatter(d.selection_ratio[sig], y[sig], marker=marker, s=22, color=color,
                   edgecolor="white", lw=0.6, zorder=4)
        ax.scatter(d.selection_ratio[~sig], y[~sig], marker=marker, s=20, facecolor="white",
                   edgecolor=color, lw=1.0, zorder=4)
    ax.set_xscale("log")
    ax.set_xlim(xmin, xmax)
    ticks = [0.03, 0.1, 0.3, 1, 3, 10, 30]
    ax.set_xticks(ticks, [f"{v:g}" for v in ticks])
    ax.xaxis.set_minor_locator(mpl.ticker.NullLocator())
    ax.set_yticks(range(len(order)), order)
    ax.tick_params(axis="y", length=0)
    ax.set_ylim(-0.6, len(order) - 0.4)
    ax.set_xlabel("Selection ratio (log scale): share of reclaimed habitat / share of the habitat map")
    ax.xaxis.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    handles = [Line2D([], [], color=c, marker=m, ms=5, lw=1.1, mec="white", mew=0.6, label=lab)
               for lab, c, m in PERIODS.values()]
    handles += [Line2D([], [], color=INK2, marker="o", ms=5, lw=0, label="95% interval excludes 1"),
                Line2D([], [], color=INK2, marker="o", ms=5, lw=0, mfc="white", label="95% interval includes 1")]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0, 1), ncols=2)
    return fig


FIGURES = {
    "fig1_annual": fig_annual,
    "fig2_map": fig_map,
    "fig3_composition": fig_composition,
    "fig4_habitat": fig_habitat,
    "fig5_selection": fig_selection,
    "fig6_hotspots": fig_hotspots,
}


def save(fig, name):
    """PDF and SVG keep the text editable; the PNG is for embedding in the report."""
    for ext in ("pdf", "svg"):
        fig.savefig(FIG / f"{name}.{ext}")
    fig.savefig(FIG / f"{name}.png", dpi=300)


def run(cfg):
    for name, make in FIGURES.items():
        fig = make(cfg)
        save(fig, name)
        plt.close(fig)
        print(f"wrote outputs/figures/{name}.pdf / .svg / .png")


if __name__ == "__main__":
    run(config.load())
