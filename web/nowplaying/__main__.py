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
    parser.add_argument(
        "--dlna-port",
        type=int,
        default=(int(os.environ["DLNA_PORT"]) if os.environ.get("ENABLE_DLNA") == "1" else None),
        help="Port of the local UPnP renderer to watch",
    )
    parser.add_argument(
        "--dlna-url",
        default=os.environ.get("DLNA_URL") or None,
        help="Base URL of the local UPnP renderer, e.g. http://127.0.0.1:49494",
    )
    parser.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "info"))
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run(args.host, args.port, args.poll_interval, args.dlna_port, args.dlna_url)


if __name__ == "__main__":
    main()
