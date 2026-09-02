"""Publish a fake org.bluez with one connected A2DP device, for developing the UI.

Real Bluetooth needs hardware, so this stands in for BlueZ on the system bus: it
exports a Device1 and a MediaPlayer1 at the paths BlueZ would use, and lets
dbus-fast's own ObjectManager advertise them. That exercises the BlueZ reader in
nowplaying/players.py -- object walking, the device-alias lookup and the
millisecond-to-microsecond conversion -- without a radio.

Run it inside the container, with ENABLE_BLUETOOTH left off so the real
bluetoothd is not competing for the name:

    docker exec -d np /opt/venv/bin/python /scripts/fake_bluez.py
"""

from __future__ import annotations

import argparse
import asyncio

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.constants import PropertyAccess
from dbus_fast.service import ServiceInterface, dbus_property, method

ADAPTER_PATH = "/org/bluez/hci0"
DEVICE_PATH = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
PLAYER_PATH = f"{DEVICE_PATH}/player0"


class AgentManager(ServiceInterface):
    """Enough of org.bluez.AgentManager1 for a pairing agent to register."""

    def __init__(self) -> None:
        """Create the agent manager interface."""
        super().__init__("org.bluez.AgentManager1")
        self.registered: list[tuple[str, str]] = []
        self.default: str | None = None

    @method()
    def RegisterAgent(self, agent: "o", capability: "s"):  # noqa: N802
        """Record the agent BlueZ would have registered."""
        self.registered.append((agent, capability))
        print(f"RegisterAgent {agent} capability={capability}", flush=True)

    @method()
    def RequestDefaultAgent(self, agent: "o"):  # noqa: N802
        """Record the agent being made default."""
        self.default = agent
        print(f"RequestDefaultAgent {agent}", flush=True)

    @method()
    def UnregisterAgent(self, agent: "o"):  # noqa: N802
        """Forget a previously registered agent."""
        self.registered = [a for a in self.registered if a[0] != agent]


class Adapter(ServiceInterface):
    """A writable org.bluez.Adapter1, so agent property sets can be observed."""

    def __init__(self) -> None:
        """Create the adapter interface."""
        super().__init__("org.bluez.Adapter1")
        self.state: dict[str, object] = {
            "Powered": False,
            "Alias": "",
            "Pairable": False,
            "Discoverable": False,
        }

    @dbus_property(access=PropertyAccess.READWRITE)
    def Powered(self) -> "b":  # noqa: N802
        """Whether the radio is on."""
        return bool(self.state["Powered"])

    @Powered.setter
    def Powered(self, value: "b"):  # noqa: N802
        self.state["Powered"] = value
        print(f"Adapter.Powered = {value}", flush=True)

    @dbus_property(access=PropertyAccess.READWRITE)
    def Alias(self) -> "s":  # noqa: N802
        """The adapter's advertised name."""
        return str(self.state["Alias"])

    @Alias.setter
    def Alias(self, value: "s"):  # noqa: N802
        self.state["Alias"] = value
        print(f"Adapter.Alias = {value}", flush=True)

    @dbus_property(access=PropertyAccess.READWRITE)
    def Pairable(self) -> "b":  # noqa: N802
        """Whether new devices may pair."""
        return bool(self.state["Pairable"])

    @Pairable.setter
    def Pairable(self, value: "b"):  # noqa: N802
        self.state["Pairable"] = value
        print(f"Adapter.Pairable = {value}", flush=True)

    @dbus_property(access=PropertyAccess.READWRITE)
    def Discoverable(self) -> "b":  # noqa: N802
        """Whether the adapter answers inquiries."""
        return bool(self.state["Discoverable"])

    @Discoverable.setter
    def Discoverable(self, value: "b"):  # noqa: N802
        self.state["Discoverable"] = value
        print(f"Adapter.Discoverable = {value}", flush=True)


class Device(ServiceInterface):
    """A paired, connected org.bluez.Device1 that starts out untrusted."""

    def __init__(self, alias: str) -> None:
        """Create the device interface with the name the UI should show."""
        super().__init__("org.bluez.Device1")
        self._alias = alias
        self.trusted = False

    @dbus_property(access=PropertyAccess.READ)
    def Alias(self) -> "s":  # noqa: N802
        """Friendly device name, e.g. the phone's name."""
        return self._alias

    @dbus_property(access=PropertyAccess.READ)
    def Connected(self) -> "b":  # noqa: N802
        """Whether the device is currently connected."""
        return True

    @dbus_property(access=PropertyAccess.READ)
    def Paired(self) -> "b":  # noqa: N802
        """Whether the device is paired."""
        return True

    @dbus_property(access=PropertyAccess.READWRITE)
    def Trusted(self) -> "b":  # noqa: N802
        """Whether the device may reconnect on its own."""
        return self.trusted

    @Trusted.setter
    def Trusted(self, value: "b"):  # noqa: N802
        self.trusted = value
        print(f"Device.Trusted = {value}", flush=True)


class MediaPlayer(ServiceInterface):
    """An org.bluez.MediaPlayer1 carrying AVRCP metadata."""

    def __init__(self, title: str, artist: str, album: str, length_ms: int, status: str) -> None:
        """Create a player stuck on one track."""
        super().__init__("org.bluez.MediaPlayer1")
        self._track = {
            "Title": Variant("s", title),
            "Artist": Variant("s", artist),
            "Album": Variant("s", album),
            # BlueZ reports Duration in milliseconds.
            "Duration": Variant("u", length_ms),
        }
        self._status = status
        self.position = 0

    @dbus_property(access=PropertyAccess.READ)
    def Name(self) -> "s":  # noqa: N802
        """AVRCP player name, usually something generic."""
        return "Music"

    @dbus_property(access=PropertyAccess.READ)
    def Status(self) -> "s":  # noqa: N802
        """Lowercase playback status, as BlueZ reports it."""
        return self._status

    @dbus_property(access=PropertyAccess.READ)
    def Position(self) -> "u":  # noqa: N802
        """Playback position in milliseconds."""
        return self.position

    @dbus_property(access=PropertyAccess.READ)
    def Track(self) -> "a{sv}":  # noqa: N802
        """The current track's AVRCP metadata."""
        return self._track


async def main() -> None:
    """Export the fake tree and advance the track position once a second."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alias", default="Test Phone", help="device name, as shown in the UI")
    parser.add_argument("--title", default="Bluetooth Track")
    parser.add_argument("--artist", default="Bluetooth Artist")
    parser.add_argument("--album", default="Bluetooth Album")
    parser.add_argument("--length", type=int, default=185, help="track length in seconds")
    parser.add_argument("--status", default="playing", help="playing, paused or stopped")
    args = parser.parse_args()

    length_ms = args.length * 1000
    player = MediaPlayer(args.title, args.artist, args.album, length_ms, args.status)

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    bus.export("/org/bluez", AgentManager())
    bus.export(ADAPTER_PATH, Adapter())
    bus.export(DEVICE_PATH, Device(args.alias))
    bus.export(PLAYER_PATH, player)
    await bus.request_name("org.bluez")
    print(f"exported a fake org.bluez with {args.alias!r} playing {args.title!r}")

    while True:
        await asyncio.sleep(1)
        player.position = (player.position + 1000) % length_ms


if __name__ == "__main__":
    asyncio.run(main())
