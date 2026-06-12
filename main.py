"""CLI entry point.

Usage:
  python main.py scrape          # run scraper once and save
  python main.py serve           # start web dashboard
  python main.py scrape serve    # scrape then start server
"""

import sys


def cli():
    args = set(sys.argv[1:]) or {"--help"}

    if "--help" in args or "-h" in args:
        print(__doc__)
        return

    if "scrape" in args:
        from scraper import run_scrape
        from storage import save_run
        print("Running scraper...")
        run = run_scrape()
        path = save_run(run)
        total = sum(p["sample_count"] for p in run["projects"])
        print(f"Saved {total} samples → {path}")

    if "serve" in args:
        from server import main as serve
        serve()

    if not args & {"scrape", "serve"}:
        print(f"Unknown command: {sys.argv[1:]}")
        print(__doc__)


if __name__ == "__main__":
    cli()
