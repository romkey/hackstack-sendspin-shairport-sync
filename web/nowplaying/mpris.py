"""Poll every MPRIS player on the session bus and expose a single combined state.

Shairport Sync (built with ``--with-mpris-interface``) and the Sendspin daemon both
publish an ``org.mpris.MediaPlayer2.*`` name, so one observer covers both sources.
Polling rather than subscribing to ``PropertiesChanged`` keeps this resilient to
players that appear, vanish, or emit signals inconsistently.
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
IFACE_ROOT = "org.mpris.MediaPlayer2"
IFACE_PLAYER = "org.mpris.MediaPlayer2.Player"
IFACE_PROPS = "org.freedesktop.DBus.Properties"

# Bus-name suffix -> the label the UI uses for the source.
_KNOWN_SOURCES = {
    "shairportsync": "airplay",
    "sendspin": "sendspin",
}


@dataclass
class Player:
    """A snapshot of one MPRIS player."""

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


class MprisWatcher:
    """Keeps a live view of every MPRIS player on the session bus."""

    def __init__(self, poll_interval: float = 1.0) -> None:
        """Create a watcher; call :meth:`start` to connect."""
        self._poll_interval = poll_interval
        self._bus: MessageBus | None = None
        self._state = State()
        self._task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[State]] = set()

    @property
    def state(self) -> State:
        """The most recent snapshot."""
        return self._state

    async def start(self) -> None:
        """Begin polling in the background."""
        self._task = asyncio.create_task(self._run(), name="mpris-watcher")

    async def stop(self) -> None:
        """Stop polling and disconnect."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None

    def subscribe(self) -> asyncio.Queue[State]:
        """Register for state changes; the queue receives a snapshot per change."""
        queue: asyncio.Queue[State] = asyncio.Queue(maxsize=8)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[State]) -> None:
        """Undo :meth:`subscribe`."""
        self._subscribers.discard(queue)

    # -- internals ----------------------------------------------------------

    async def _run(self) -> None:
        previous: dict[str, Any] | None = None
        while True:
            try:
                bus = await self._ensure_bus()
                state = await self._poll(bus)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- never let the poller die
                _LOGGER.warning("MPRIS poll failed: %s", exc)
                if self._bus is not None:
                    with contextlib.suppress(Exception):
                        self._bus.disconnect()
                    self._bus = None
                state = State()

            self._state = state
            current = state.to_dict()
            if current != previous:
                previous = current
                self._publish(state)

            await asyncio.sleep(self._poll_interval)

    async def _ensure_bus(self) -> MessageBus:
        if self._bus is None or not self._bus.connected:
            _LOGGER.info("connecting to the D-Bus session bus")
            self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
        return self._bus

    def _publish(self, state: State) -> None:
        for queue in list(self._subscribers):
            if queue.full():  # a stalled client must not block the poller
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(state)

    async def _call(
        self,
        bus: MessageBus,
        destination: str,
        interface: str,
        member: str,
        signature: str = "",
        body: list[Any] | None = None,
        path: str = MPRIS_PATH,
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

    async def _poll(self, bus: MessageBus) -> State:
        names = await self._call(
            bus,
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "ListNames",
            path="/org/freedesktop/DBus",
        )
        bus_names = sorted(n for n in names if n.startswith(MPRIS_PREFIX))

        players: list[Player] = []
        for bus_name in bus_names:
            try:
                players.append(await self._read_player(bus, bus_name))
            except Exception as exc:  # noqa: BLE001 -- one bad player must not hide the rest
                _LOGGER.debug("could not read %s: %s", bus_name, exc)

        return State(players=players, active=self._pick_active(players))

    async def _read_player(self, bus: MessageBus, bus_name: str) -> Player:
        props = _unwrap(
            await self._call(
                bus, bus_name, IFACE_PROPS, "GetAll", signature="s", body=[IFACE_PLAYER]
            )
        )

        identity = _source_for(bus_name)
        with contextlib.suppress(Exception):
            root = _unwrap(
                await self._call(
                    bus, bus_name, IFACE_PROPS, "GetAll", signature="s", body=[IFACE_ROOT]
                )
            )
            identity = root.get("Identity") or identity

        metadata = props.get("Metadata") or {}
        length = metadata.get("mpris:length")
        position = props.get("Position")
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
            length_us=int(length) if isinstance(length, (int, float)) and length > 0 else None,
            position_us=(
                int(position) if isinstance(position, (int, float)) and position > 0 else None
            ),
            volume=float(volume) if isinstance(volume, (int, float)) else None,
        )

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
