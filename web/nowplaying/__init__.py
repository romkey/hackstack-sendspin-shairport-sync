"""Unified now-playing web UI for Shairport Sync and Sendspin."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("nowplaying")
except PackageNotFoundError:  # running from a source checkout, not installed
    __version__ = "0.0.0+dev"
