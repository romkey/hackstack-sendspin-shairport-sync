"""Command-line entry point: ``python -m nowplaying``."""

from __future__ import annotations

import argparse
import logging
import os

from nowplaying.server import run


def main() -> None:
    """Parse arguments and start the server."""
    parser = argparse.ArgumentParser(prog="nowplaying", description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")  # noqa: S104 -- container-facing by design
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEB_PORT", "8080")))
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run(args.host, args.port, args.poll_interval)


if __name__ == "__main__":
    main()
