"""Stage 1, data collection.

    python -m reclaim.collect static          habitat maps, land area, boundary (no account needed)
    python -m reclaim.collect gee --test      one tile, one year: check Earth Engine works
    python -m reclaim.collect gee             all Sentinel-2 and Landsat 8 annual composites
    python -m reclaim.collect gee --sources s1    optional Sentinel-1 cross-check
"""
import argparse

from . import config


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("static")
    s.add_argument("--overwrite", action="store_true")
    g = sub.add_parser("gee")
    g.add_argument("--sources", nargs="+", default=["s2", "landsat"], choices=["s2", "landsat", "s1"])
    g.add_argument("--years", nargs="+", type=int, help="only these years")
    g.add_argument("--test", action="store_true", help="one tile of the latest year into data/raw/_test/")
    g.add_argument("--workers", type=int, default=6)
    g.add_argument("--overwrite", action="store_true")
    args = p.parse_args(argv)
    cfg = config.load()

    if args.cmd == "static":
        from . import static
        static.run(cfg, overwrite=args.overwrite)
    else:
        from . import gee
        gee.run(cfg, sources=args.sources, years=args.years, test=args.test,
                workers=args.workers, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
