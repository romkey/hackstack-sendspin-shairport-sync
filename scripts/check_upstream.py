#!/usr/bin/env python3
"""Compare versions.env against the latest upstream releases and update it in place.

Prints a short human-readable summary to stdout and, when running under GitHub
Actions, writes ``changed``/``summary`` to ``$GITHUB_OUTPUT``.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

VERSIONS_FILE = Path(__file__).resolve().parent.parent / "versions.env"

SOURCES = {
    "SHAIRPORT_SYNC_VERSION": ("github", "mikebrady/shairport-sync"),
    "NQPTP_VERSION": ("github", "mikebrady/nqptp"),
    "SENDSPIN_VERSION": ("pypi", "sendspin"),
    "SPOTIFYD_VERSION": ("github", "Spotifyd/spotifyd"),
}


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "sendspin-shareplay-watch"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 -- fixed hosts
        return json.load(response)


def latest_version(kind: str, name: str) -> str:
    """Return the newest published version for one upstream project."""
    if kind == "github":
        tag = _get_json(f"https://api.github.com/repos/{name}/releases/latest")["tag_name"]
        return tag.lstrip("v")
    if kind == "pypi":
        return _get_json(f"https://pypi.org/pypi/{name}/json")["info"]["version"]
    raise ValueError(f"unknown source kind: {kind}")


def read_versions(text: str) -> dict[str, str]:
    """Parse the KEY=VALUE lines of versions.env."""
    return dict(
        re.findall(r"^([A-Z_]+)=(.*)$", text, flags=re.MULTILINE),
    )


def main() -> int:
    """Check every upstream and rewrite versions.env if anything moved."""
    text = VERSIONS_FILE.read_text()
    current = read_versions(text)

    changes: list[str] = []
    for key, (kind, name) in SOURCES.items():
        try:
            newest = latest_version(kind, name)
        except (urllib.error.URLError, KeyError, TimeoutError) as exc:
            print(f"warning: could not check {name}: {exc}", file=sys.stderr)
            continue

        have = current.get(key, "")
        if newest and newest != have:
            changes.append(f"{name}: {have or 'unset'} -> {newest}")
            text = re.sub(rf"^{key}=.*$", f"{key}={newest}", text, flags=re.MULTILINE)
        else:
            print(f"{name}: up to date ({have})")

    if changes:
        VERSIONS_FILE.write_text(text)
        for change in changes:
            print(f"updated {change}")

    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as handle:
            handle.write(f"changed={'true' if changes else 'false'}\n")
            handle.write(f"summary={'; '.join(changes)}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
