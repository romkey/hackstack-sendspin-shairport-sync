"""Collect what every attached player is doing into one combined state.

Two sources feed this:

* **MPRIS on the session bus** -- Shairport Sync (built with ``--with-mpris-interface``)
  and the Sendspin daemon each publish an ``org.mpris.MediaPlayer2.*`` name.
* **BlueZ on the system bus** -- a phone connected over A2DP shows up as an
  ``org.bluez.MediaPlayer1`` object carrying AVRCP metadata.

Both are normalised into :class:`Player`, so the UI does not care where a track
came from. Polling rather than subscribing to ``PropertiesChanged`` keeps this
resilient to players that appear, vanish, or signal inconsistently.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

_LOGGER = logging.getLogger(__name__)

MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
IFACE_MPRIS_ROOT = "org.mpris.MediaPlayer2"
IFACE_MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
IFACE_PROPS = "org.freedesktop.DBus.Properties"
IFACE_OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"

BLUEZ_SERVICE = "org.bluez"
IFACE_BLUEZ_PLAYER = "org.bluez.MediaPlayer1"
IFACE_BLUEZ_DEVICE = "org.bluez.Device1"

# MPRIS bus-name suffix -> the label the UI uses for the source.
_KNOWN_SOURCES = {
    "shairportsync": "airplay",
    "sendspin": "sendspin",
}

# BlueZ reports lowercase status strings; MPRIS uses capitalised ones.
_BLUEZ_STATUS = {
    "playing": "Playing",
    "paused": "Paused",
    "stopped": "Stopped",
    "forward-seek": "Playing",
    "reverse-seek": "Playing",
    "error": "Stopped",
}


@dataclass
class Player:
    """A snapshot of one player, whatever protocol it came from."""

    bus_name: str
    source: str
    identity: str
    status: str = "Stopped"
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    art_url: str | None = None
    length_us: int | None = None
    position_us: int | None = None
    volume: float | None = None

    @property
    def is_playing(self) -> bool:
        """Whether this player reports itself as actively playing."""
        return self.status == "Playing"

    @property
    def has_track(self) -> bool:
        """Whether this player knows anything about a current track."""
        return bool(self.title or self.artist or self.album)


@dataclass
class State:
    """Everything the UI needs in one object."""

    players: list[Player] = field(default_factory=list)
    active: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render as JSON-serialisable data."""
        return {
            "players": [asdict(p) for p in self.players],
            "active": self.active,
        }


def _unwrap(value: Any) -> Any:
    """Recursively strip ``Variant`` wrappers out of a D-Bus value."""
    if isinstance(value, Variant):
        return _unwrap(value.value)
    if isinstance(value, list):
        return [_unwrap(v) for v in value]
    if isinstance(value, dict):
        return {k: _unwrap(v) for k, v in value.items()}
    return value


def _source_for(bus_name: str) -> str:
    suffix = bus_name[len(MPRIS_PREFIX) :].split(".")[0].lower()
    return _KNOWN_SOURCES.get(suffix, "other")


def _first_str(value: Any) -> str | None:
    """MPRIS artist fields are string arrays; titles are plain strings."""
    if isinstance(value, list):
        joined = ", ".join(str(v) for v in value if v)
        return joined or None
    if isinstance(value, str):
        return value or None
    return None


def _positive_int(value: Any, scale: int = 1) -> int | None:
    """Return a positive integer scaled by ``scale``, or None if not usable."""
    if isinstance(value, (int, float)) and value > 0:
        return int(value) * scale
    return None


class PlayerWatcher:
    """Keeps a live view of every player on the session and system buses."""

    def __init__(self, poll_interval: float = 1.0) -> None:
        """Create a watcher; call :meth:`start` to connect."""
        self._poll_interval = poll_interval
        self._session_bus: MessageBus | None = None
        self._system_bus: MessageBus | None = None
        self._state = State()
        self._task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[State]] = set()
        self._bluez_warned = False

    @property
    def state(self) -> State:
        """The most recent snapshot."""
        return self._state

    async def start(self) -> None:
        """Begin polling in the background."""
        self._task = asyncio.create_task(self._run(), name="player-watcher")

    async def stop(self) -> None:
        """Stop polling and disconnect."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for bus in (self._session_bus, self._system_bus):
            if bus is not None:
                with contextlib.suppress(Exception):
                    bus.disconnect()
        self._session_bus = self._system_bus = None

    def subscribe(self) -> asyncio.Queue[State]:
        """Register for state changes; the queue receives a snapshot per change."""
        queue: asyncio.Queue[State] = asyncio.Queue(maxsize=8)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[State]) -> None:
        """Undo :meth:`subscribe`."""
        self._subscribers.discard(queue)

    # -- polling loop -------------------------------------------------------

    async def _run(self) -> None:
        previous: dict[str, Any] | None = None
        while True:
            players: list[Player] = []

            try:
                players.extend(await self._poll_mpris())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- never let the poller die
                _LOGGER.warning("MPRIS poll failed: %s", exc)
                await self._drop_bus("session")

            try:
                players.extend(await self._poll_bluez())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("BlueZ poll failed: %s", exc)
                await self._drop_bus("system")

            state = State(players=players, active=self._pick_active(players))
            self._state = state

            current = state.to_dict()
            if current != previous:
                previous = current
                self._publish(state)

            await asyncio.sleep(self._poll_interval)

    async def _drop_bus(self, which: str) -> None:
        bus = self._session_bus if which == "session" else self._system_bus
        if bus is not None:
            with contextlib.suppress(Exception):
                bus.disconnect()
        if which == "session":
            self._session_bus = None
        else:
            self._system_bus = None

    async def _session(self) -> MessageBus:
        if self._session_bus is None or not self._session_bus.connected:
            _LOGGER.info("connecting to the D-Bus session bus")
            self._session_bus = await MessageBus(bus_type=BusType.SESSION).connect()
        return self._session_bus

    async def _system(self) -> MessageBus:
        if self._system_bus is None or not self._system_bus.connected:
            _LOGGER.debug("connecting to the D-Bus system bus")
            self._system_bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        return self._system_bus

    def _publish(self, state: State) -> None:
        for queue in list(self._subscribers):
            if queue.full():  # a stalled client must not block the poller
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(state)

    @staticmethod
    async def _call(
        bus: MessageBus,
        destination: str,
        interface: str,
        member: str,
        path: str,
        signature: str = "",
        body: list[Any] | None = None,
    ) -> Any:
        reply = await bus.call(
            Message(
                destination=destination,
                path=path,
                interface=interface,
                member=member,
                signature=signature,
                body=body or [],
            )
        )
        if reply is None or reply.message_type != MessageType.METHOD_RETURN:
            raise RuntimeError(f"{destination}.{member} failed: {getattr(reply, 'body', None)}")
        return reply.body[0] if reply.body else None

    # -- MPRIS (session bus) ------------------------------------------------

    async def _poll_mpris(self) -> list[Player]:
        bus = await self._session()
        names = await self._call(
            bus,
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "ListNames",
            path="/org/freedesktop/DBus",
        )

        players: list[Player] = []
        for bus_name in sorted(n for n in names if n.startswith(MPRIS_PREFIX)):
            try:
                players.append(await self._read_mpris_player(bus, bus_name))
            except Exception as exc:  # noqa: BLE001 -- one bad player must not hide the rest
                _LOGGER.debug("could not read %s: %s", bus_name, exc)
        return players

    async def _read_mpris_player(self, bus: MessageBus, bus_name: str) -> Player:
        props = _unwrap(
            await self._call(
                bus,
                bus_name,
                IFACE_PROPS,
                "GetAll",
                path=MPRIS_PATH,
                signature="s",
                body=[IFACE_MPRIS_PLAYER],
            )
        )

        identity = _source_for(bus_name)
        with contextlib.suppress(Exception):
            root = _unwrap(
                await self._call(
                    bus,
                    bus_name,
                    IFACE_PROPS,
                    "GetAll",
                    path=MPRIS_PATH,
                    signature="s",
                    body=[IFACE_MPRIS_ROOT],
                )
            )
            identity = root.get("Identity") or identity

        metadata = props.get("Metadata") or {}
        volume = props.get("Volume")

        return Player(
            bus_name=bus_name,
            source=_source_for(bus_name),
            identity=str(identity),
            status=str(props.get("PlaybackStatus") or "Stopped"),
            title=_first_str(metadata.get("xesam:title")),
            artist=_first_str(metadata.get("xesam:artist"))
            or _first_str(metadata.get("xesam:albumArtist")),
            album=_first_str(metadata.get("xesam:album")),
            art_url=_first_str(metadata.get("mpris:artUrl")),
            length_us=_positive_int(metadata.get("mpris:length")),
            position_us=_positive_int(props.get("Position")),
            volume=float(volume) if isinstance(volume, (int, float)) else None,
        )

    # -- BlueZ / AVRCP (system bus) -----------------------------------------

    async def _poll_bluez(self) -> list[Player]:
        """Read AVRCP metadata for any phone connected over A2DP.

        Returns an empty list when BlueZ is not running, which is the normal
        case with ENABLE_BLUETOOTH=0.
        """
        bus = await self._system()

        # Ask whether anything owns the name before calling it. Calling org.bluez
        # directly makes the bus try to *activate* it from its .service file, and
        # with ENABLE_BLUETOOTH=0 that fails noisily once per poll.
        owned = await self._call(
            bus,
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            path="/org/freedesktop/DBus",
            signature="s",
            body=[BLUEZ_SERVICE],
        )
        if not owned:
            if not self._bluez_warned:
                _LOGGER.debug("BlueZ is not on the system bus; skipping Bluetooth")
                self._bluez_warned = True
            return []
        self._bluez_warned = False

        objects = _unwrap(
            await self._call(
                bus, BLUEZ_SERVICE, IFACE_OBJECT_MANAGER, "GetManagedObjects", path="/"
            )
        )

        # Device aliases give a far nicer label than the AVRCP player name,
        # which is often just "Music" or missing entirely.
        aliases = {
            path: ifaces[IFACE_BLUEZ_DEVICE].get("Alias")
            for path, ifaces in objects.items()
            if IFACE_BLUEZ_DEVICE in ifaces
        }

        players: list[Player] = []
        for path, ifaces in sorted(objects.items()):
            if IFACE_BLUEZ_PLAYER not in ifaces:
                continue
            players.append(self._read_bluez_player(path, ifaces[IFACE_BLUEZ_PLAYER], aliases))
        return players

    @staticmethod
    def _read_bluez_player(
        path: str, props: dict[str, Any], aliases: dict[str, str | None]
    ) -> Player:
        # A player lives at .../dev_AA_BB_CC/playerN, so its device is the parent.
        device_path = path.rsplit("/", 1)[0]
        identity = aliases.get(device_path) or props.get("Name") or "Bluetooth"

        track = props.get("Track") or {}
        return Player(
            bus_name=path,
            source="bluetooth",
            identity=str(identity),
            status=_BLUEZ_STATUS.get(str(props.get("Status", "")).lower(), "Stopped"),
            title=_first_str(track.get("Title")),
            artist=_first_str(track.get("Artist")),
            album=_first_str(track.get("Album")),
            # AVRCP 1.6 can carry cover art, but BlueZ does not expose it.
            art_url=None,
            # BlueZ reports milliseconds; everything here is microseconds.
            length_us=_positive_int(track.get("Duration"), 1000),
            position_us=_positive_int(props.get("Position"), 1000),
            volume=None,
        )

    # -- picking what to show ------------------------------------------------

    @staticmethod
    def _pick_active(players: list[Player]) -> str | None:
        for predicate in (
            lambda p: p.is_playing and p.has_track,
            lambda p: p.is_playing,
            lambda p: p.status == "Paused",
            lambda p: p.has_track,
        ):
            for player in players:
                if predicate(player):
                    return player.bus_name
        return players[0].bus_name if players else None
