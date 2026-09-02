"""Collect what every attached player is doing into one combined state.

Two sources feed this:

* **MPRIS on the session bus** -- Shairport Sync (built with ``--with-mpris-interface``)
  and the Sendspin daemon each publish an ``org.mpris.MediaPlayer2.*`` name.
* **BlueZ on the system bus** -- a phone connected over A2DP shows up as an
  ``org.bluez.MediaPlayer1`` object carrying AVRCP metadata.
* **UPnP/DLNA over HTTP** -- gmediarender has no bus interface at all, so it is
  queried as any control point would: SOAP calls to its own AVTransport service.

Both are normalised into :class:`Player`, so the UI does not care where a track
came from. Polling rather than subscribing to ``PropertiesChanged`` keeps this
resilient to players that appear, vanish, or signal inconsistently.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urljoin

import aiohttp
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

AVTRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERINGCONTROL = "urn:schemas-upnp-org:service:RenderingControl:1"
DIDL_NS = {
    "didl": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
}

# MPRIS bus-name suffix -> the label the UI uses for the source.
_KNOWN_SOURCES = {
    "shairportsync": "airplay",
    "sendspin": "sendspin",
    "spotifyd": "spotify",
}

# UPnP transport states -> the MPRIS-style status the UI expects.
_UPNP_STATUS = {
    "PLAYING": "Playing",
    "TRANSITIONING": "Playing",
    "PAUSED_PLAYBACK": "Paused",
    "PAUSED_RECORDING": "Paused",
    "STOPPED": "Stopped",
    "NO_MEDIA_PRESENT": "Stopped",
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

    id: str
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


def _local_ip() -> str | None:
    """Best guess at this machine's primary address.

    libupnp binds the renderer to one interface rather than to every address, so
    the loopback address usually will not reach it. Connecting a UDP socket sends
    nothing; it just makes the kernel pick the outbound interface.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def _upnp_duration_us(text: str | None) -> int | None:
    """Parse a UPnP ``H:MM:SS[.frac]`` duration into microseconds."""
    if not text or text in ("NOT_IMPLEMENTED", "0:00:00"):
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:  # handles H:MM:SS and MM:SS alike
        seconds = seconds * 60 + part
    return int(seconds * 1_000_000) or None


def _positive_int(value: Any, scale: int = 1) -> int | None:
    """Return a positive integer scaled by ``scale``, or None if not usable."""
    if isinstance(value, (int, float)) and value > 0:
        return int(value) * scale
    return None


class PlayerWatcher:
    """Keeps a live view of every player on the session and system buses."""

    def __init__(
        self,
        poll_interval: float = 1.0,
        dlna_port: int | None = None,
        dlna_url: str | None = None,
    ) -> None:
        """Create a watcher; call :meth:`start` to connect.

        Pass ``dlna_port`` to look for a local UPnP renderer, or ``dlna_url`` to
        point at one explicitly. With neither, DLNA is skipped entirely.
        """
        self._poll_interval = poll_interval
        self._dlna_port = dlna_port
        self._dlna_url = dlna_url.rstrip("/") if dlna_url else None
        self._dlna_control: str | None = None
        self._dlna_rendering: str | None = None
        self._dlna_name = "DLNA"
        self._http: aiohttp.ClientSession | None = None
        self._dlna_warned = False
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
        if self._http is not None and not self._http.closed:
            await self._http.close()
        self._http = None

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

            try:
                players.extend(await self._poll_dlna())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("DLNA poll failed: %s", exc)
                # Force rediscovery in case the renderer restarted on a new port.
                self._dlna_control = None
                self._dlna_rendering = None

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
            id=bus_name,
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
            id=path,
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

    # -- UPnP / DLNA (plain HTTP) -------------------------------------------

    async def _http_session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            # libupnp closes the socket after every response, so a pooled
            # connection is always dead by the time the next call reuses it.
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=4),
                connector=aiohttp.TCPConnector(force_close=True),
            )
        return self._http

    def _dlna_bases(self) -> list[str]:
        """Base URLs worth trying, most likely first."""
        if self._dlna_url:
            return [self._dlna_url]
        bases = []
        local = _local_ip()
        if local:
            bases.append(f"http://{local}:{self._dlna_port}")
        bases.append(f"http://127.0.0.1:{self._dlna_port}")
        return bases

    async def _control_url(self, session: aiohttp.ClientSession) -> str:
        """Find the renderer's AVTransport control URL, and remember it."""
        if self._dlna_control is not None:
            return self._dlna_control

        last_error: Exception | None = None
        for base in self._dlna_bases():
            try:
                return await self._discover(session, base)
            except (aiohttp.ClientError, TimeoutError, OSError, ET.ParseError) as exc:
                last_error = exc
        raise last_error or RuntimeError("no UPnP renderer found")

    async def _discover(self, session: aiohttp.ClientSession, base: str) -> str:
        description = f"{base}/description.xml"
        async with session.get(description) as response:
            response.raise_for_status()
            root = ET.fromstring(await response.text())

        control: str | None = None
        rendering: str | None = None
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "friendlyName" and element.text:
                self._dlna_name = element.text
            if tag != "service":
                continue
            fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "") for child in element}
            if fields.get("serviceType") == AVTRANSPORT:
                control = fields.get("controlURL")
            elif fields.get("serviceType") == RENDERINGCONTROL:
                rendering = fields.get("controlURL")

        if not control:
            raise RuntimeError(f"no AVTransport service in {description}")

        # RenderingControl is optional in principle; volume is skipped without it.
        self._dlna_rendering = urljoin(base + "/", rendering) if rendering else None
        self._dlna_control = urljoin(base + "/", control)
        _LOGGER.info("found UPnP renderer %r at %s", self._dlna_name, self._dlna_control)
        return self._dlna_control

    async def _soap_rendering(
        self, session: aiohttp.ClientSession, action: str, extra: str
    ) -> dict[str, str]:
        """Call a RenderingControl action, discovered alongside AVTransport."""
        if self._dlna_rendering is None:
            raise RuntimeError("this renderer exposes no RenderingControl service")
        return await self._soap(
            session, self._dlna_rendering, action, extra, service=RENDERINGCONTROL
        )

    async def _soap(
        self,
        session: aiohttp.ClientSession,
        url: str,
        action: str,
        extra: str = "",
        service: str = AVTRANSPORT,
    ) -> dict[str, str]:
        """Call one AVTransport action and flatten the response."""
        body = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            f'<u:{action} xmlns:u="{service}"><InstanceID>0</InstanceID>{extra}</u:{action}>'
            "</s:Body></s:Envelope>"
        )
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{service}#{action}"',
        }
        async with session.post(url, data=body.encode(), headers=headers) as response:
            response.raise_for_status()
            root = ET.fromstring(await response.text())

        for element in root.iter():
            if element.tag.rsplit("}", 1)[-1] == f"{action}Response":
                return {c.tag.rsplit("}", 1)[-1]: (c.text or "") for c in element}
        raise RuntimeError(f"no {action}Response in the reply")

    @staticmethod
    def _parse_didl(text: str) -> dict[str, Any]:
        """Pull the useful fields out of a DIDL-Lite metadata document."""
        if not text.strip():
            return {}
        try:
            item = ET.fromstring(text).find("didl:item", DIDL_NS)
        except ET.ParseError:
            return {}
        if item is None:
            return {}

        def text_of(path: str) -> str | None:
            found = item.find(path, DIDL_NS)
            return (found.text or None) if found is not None else None

        res = item.find("didl:res", DIDL_NS)
        return {
            "title": text_of("dc:title"),
            "artist": text_of("upnp:artist") or text_of("dc:creator"),
            "album": text_of("upnp:album"),
            "art_url": text_of("upnp:albumArtURI"),
            "length_us": _upnp_duration_us(res.get("duration") if res is not None else None),
        }

    async def _poll_dlna(self) -> list[Player]:
        """Query the local UPnP renderer the same way any control point would.

        Returns an empty list when no renderer is configured or reachable, which
        is the normal case with ENABLE_DLNA=0.
        """
        if not self._dlna_url and not self._dlna_port:
            return []

        session = await self._http_session()
        try:
            control = await self._control_url(session)
            transport = await self._soap(session, control, "GetTransportInfo")
        except (aiohttp.ClientError, TimeoutError, OSError):
            if not self._dlna_warned:
                _LOGGER.debug("no UPnP renderer reachable; skipping DLNA")
                self._dlna_warned = True
            self._dlna_control = None
            self._dlna_rendering = None
            return []
        self._dlna_warned = False

        status = _UPNP_STATUS.get(transport.get("CurrentTransportState", ""), "Stopped")
        player = Player(id="dlna", source="dlna", identity=self._dlna_name, status=status)
        if status == "Stopped":
            return [player]

        media = await self._soap(session, control, "GetMediaInfo")
        position = await self._soap(session, control, "GetPositionInfo")

        # Controllers put the metadata in either place depending on how they queued it.
        meta = self._parse_didl(media.get("CurrentURIMetaData", "")) or self._parse_didl(
            position.get("TrackMetaData", "")
        )

        player.title = meta.get("title")
        player.artist = meta.get("artist")
        player.album = meta.get("album")
        player.art_url = meta.get("art_url")
        player.length_us = _upnp_duration_us(position.get("TrackDuration")) or meta.get("length_us")
        player.position_us = _upnp_duration_us(position.get("RelTime"))
        return [player]

    # -- control -------------------------------------------------------------

    def _find(self, player_id: str | None) -> Player | None:
        target = player_id or self._state.active
        return next((p for p in self._state.players if p.id == target), None)

    async def set_volume(self, level: float, player_id: str | None = None) -> bool:
        """Set the volume of one player, 0.0-1.0. Returns whether it was applied."""
        player = self._find(player_id)
        if player is None:
            return False
        level = max(0.0, min(1.0, level))

        if player.source == "dlna":
            session = await self._http_session()
            control = await self._control_url(session)
            # RenderingControl is a different service from AVTransport, and it
            # takes whole percent rather than a fraction.
            await self._soap_rendering(
                session,
                control,
                "SetVolume",
                f"<Channel>Master</Channel>"
                f"<DesiredVolume>{int(round(level * 100))}</DesiredVolume>",
            )
            return True

        if player.source == "bluetooth":
            # BlueZ carries volume on MediaTransport1, not on the player, and
            # only when the peer negotiated absolute volume. Not supported here.
            return False

        bus = await self._session()
        await self._call(
            bus,
            player.id,
            IFACE_PROPS,
            "Set",
            path=MPRIS_PATH,
            signature="ssv",
            body=[IFACE_MPRIS_PLAYER, "Volume", Variant("d", level)],
        )
        return True

    async def send_command(self, command: str, player_id: str | None = None) -> bool:
        """Send a transport command to one player. Returns whether it was sent."""
        mpris = {
            "play": "Play",
            "pause": "Pause",
            "play_pause": "PlayPause",
            "stop": "Stop",
            "next": "Next",
            "previous": "Previous",
        }
        if command not in mpris:
            _LOGGER.warning("ignoring unknown command %r", command)
            return False

        player = self._find(player_id)
        if player is None:
            return False

        if player.source == "dlna":
            session = await self._http_session()
            control = await self._control_url(session)
            # UPnP has no PlayPause, so resolve it against what we last saw.
            action = command
            if action == "play_pause":
                action = "pause" if player.is_playing else "play"
            upnp = {
                "play": ("Play", "<Speed>1</Speed>"),
                "pause": ("Pause", ""),
                "stop": ("Stop", ""),
                "next": ("Next", ""),
                "previous": ("Previous", ""),
            }
            if action not in upnp:
                return False
            name, extra = upnp[action]
            await self._soap(session, control, name, extra)
            return True

        # Both MPRIS and org.bluez.MediaPlayer1 use these method names.
        interface = IFACE_BLUEZ_PLAYER if player.source == "bluetooth" else IFACE_MPRIS_PLAYER
        path = player.id if player.source == "bluetooth" else MPRIS_PATH
        destination = BLUEZ_SERVICE if player.source == "bluetooth" else player.id
        bus = await (self._system() if player.source == "bluetooth" else self._session())
        await self._call(bus, destination, interface, mpris[command], path=path)
        return True

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
                    return player.id
        return players[0].id if players else None
