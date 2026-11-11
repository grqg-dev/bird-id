"""`python -m dashboard` — run the dashboard directly."""

import argparse

from . import main

ap = argparse.ArgumentParser(description="bird-id web dashboard")
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8080)
ap.add_argument("--dev", action="store_true", help="auto-reload on code/data changes")
args = ap.parse_args()
main(args.host, args.port, dev=args.dev)
