#!/usr/bin/env python3
"""Publish a fake MPRIS player on the session bus, for developing the web UI.

Run it inside the container (it needs ``dbus-fast`` from /opt/venv):

    docker run ... -v "$PWD/scripts:/scripts:ro" ...
    docker exec -it sendspin-shareplay \\
        env DBUS_SESSION_BUS_ADDRESS=unix:path=/run/sendspin-shareplay/session_bus_socket \\
        /opt/venv/bin/python /scripts/fake_mpris.py --name Sendspin
"""

from __future__ import annotations

import argparse
import asyncio

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.constants import PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_property

MPRIS_PATH = "/org/mpris/MediaPlayer2"


class Root(ServiceInterface):
    """The org.mpris.MediaPlayer2 interface."""

    def __init__(self, identity: str) -> None:
        """Create the root interface with the given display name."""
        super().__init__("org.mpris.MediaPlayer2")
        self._identity = identity

    @dbus_property(access=PropertyAccess.READ)
    def Identity(self) -> "s":
        """Human-readable player name."""
        return self._identity


class Player(ServiceInterface):
    """The org.mpris.MediaPlayer2.Player interface."""

    def __init__(
        self,
        title: str,
        artist: str,
        album: str,
        length_us: int,
        art_url: str | None = None,
    ) -> None:
        """Create a player stuck on one track."""
        super().__init__("org.mpris.MediaPlayer2.Player")
        self._metadata = {
            "xesam:title": ("s", title),
            "xesam:artist": ("as", [artist]),
            "xesam:album": ("s", album),
            "mpris:length": ("x", length_us),
            "mpris:trackid": ("o", "/org/mpris/MediaPlayer2/Track/1"),
        }
        if art_url:
            self._metadata["mpris:artUrl"] = ("s", art_url)
        self.position = 0

    @dbus_property(access=PropertyAccess.READ)
    def PlaybackStatus(self) -> "s":
        """Always playing."""
        return "Playing"

    @dbus_property(access=PropertyAccess.READ)
    def Metadata(self) -> "a{sv}":
        """The current track."""
        return {k: Variant(sig, val) for k, (sig, val) in self._metadata.items()}

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "x":
        """Playback position in microseconds."""
        return self.position

    @dbus_property(access=PropertyAccess.READ)
    def Volume(self) -> "d":
        """Playback volume, 0.0-1.0."""
        return 0.75


async def main() -> None:
    """Export the fake player and advance its position once a second."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="FakePlayer", help="MPRIS bus-name suffix")
    parser.add_argument("--title", default="Test Track")
    parser.add_argument("--artist", default="Test Artist")
    parser.add_argument("--album", default="Test Album")
    parser.add_argument("--length", type=int, default=210, help="track length in seconds")
    parser.add_argument("--art", default=None, help="cover art as a file:// URL")
    args = parser.parse_args()

    bus = await MessageBus(bus_type=BusType.SESSION).connect()
    player = Player(args.title, args.artist, args.album, args.length * 1_000_000, args.art)
    bus.export(MPRIS_PATH, Root(args.name))
    bus.export(MPRIS_PATH, player)
    await bus.request_name(f"org.mpris.MediaPlayer2.{args.name}")
    print(f"exported org.mpris.MediaPlayer2.{args.name}")

    while True:
        await asyncio.sleep(1)
        player.position = (player.position + 1_000_000) % (args.length * 1_000_000)


if __name__ == "__main__":
    asyncio.run(main())
