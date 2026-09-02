"""Publish player state to MQTT and expose the speaker to Home Assistant.

Home Assistant has no MQTT ``media_player`` platform, so this maps the combined
state onto the platforms it does have: sensors for the track, a binary sensor
for whether anything is playing, a number for volume, buttons for transport and
restart, and diagnostic sensors for CPU, memory and temperature.

Everything is published retained, so Home Assistant has correct state after a
restart on either side, and a last-will message marks the speaker unavailable if
this process dies.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import aiomqtt

from nowplaying import __version__
from nowplaying.diagnostics import Diagnostics

if TYPE_CHECKING:
    from nowplaying.players import PlayerWatcher, State

_LOGGER = logging.getLogger(__name__)

ONLINE = "online"
OFFLINE = "offline"

# Transport buttons, as (command, Home Assistant name, icon).
BUTTONS = [
    ("play_pause", "Play/pause", "mdi:play-pause"),
    ("next", "Next track", "mdi:skip-next"),
    ("previous", "Previous track", "mdi:skip-previous"),
    ("stop", "Stop", "mdi:stop"),
]

# Diagnostic sensors, as (key, name, unit, device_class, icon).
DIAGNOSTICS = [
    ("cpu_percent", "CPU", "%", None, "mdi:cpu-64-bit"),
    ("memory_percent", "Memory", "%", None, "mdi:memory"),
    ("memory_used_mb", "Memory used", "MB", "data_size", "mdi:memory"),
    ("disk_used_percent", "Disk", "%", None, "mdi:harddisk"),
    ("cpu_temperature_c", "Temperature", "°C", "temperature", None),
    ("load_1m", "Load average", None, None, "mdi:gauge"),
    ("uptime_seconds", "Uptime", "s", "duration", "mdi:clock-outline"),
]


def slugify(value: str) -> str:
    """Reduce a name to something usable in an MQTT topic and entity id."""
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "sendspin_shareplay"


@dataclass
class MqttSettings:
    """Everything needed to talk to the broker and name ourselves."""

    host: str
    port: int = 1883
    username: str | None = None
    password: str | None = None
    tls: bool = False
    device_name: str = "sendspin-shareplay"
    device_id: str = "sendspin_shareplay"
    base_topic: str = "sendspin-shareplay"
    discovery_prefix: str = "homeassistant"
    art_base_url: str | None = None
    diagnostics_interval: float = 30.0

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> MqttSettings | None:
        """Build settings from the environment, or None when MQTT is disabled."""
        env = environ if environ is not None else dict(os.environ)
        if env.get("ENABLE_MQTT") != "1" or not env.get("MQTT_HOST"):
            return None

        name = env.get("MQTT_DEVICE_NAME") or env.get("AIRPLAY_NAME") or "sendspin-shareplay"
        device_id = env.get("MQTT_DEVICE_ID") or slugify(name)

        # Home Assistant fetches cover art over HTTP, so local file art has to be
        # proxied through our own web server at an address HA can reach.
        art_base = env.get("MQTT_ART_BASE_URL")
        if not art_base:
            from nowplaying.players import _local_ip

            address = _local_ip()
            art_base = f"http://{address}:{env.get('WEB_PORT', '8080')}" if address else None
        return cls(
            host=env["MQTT_HOST"],
            port=int(env.get("MQTT_PORT", "1883")),
            username=env.get("MQTT_USERNAME") or None,
            password=env.get("MQTT_PASSWORD") or None,
            tls=env.get("MQTT_TLS") == "1",
            device_name=name,
            device_id=device_id,
            base_topic=env.get("MQTT_BASE_TOPIC") or f"sendspin-shareplay/{device_id}",
            discovery_prefix=env.get("MQTT_DISCOVERY_PREFIX") or "homeassistant",
            art_base_url=art_base,
            diagnostics_interval=float(env.get("MQTT_DIAGNOSTICS_INTERVAL", "30")),
        )


class MqttBridge:
    """Mirrors player state onto MQTT and applies commands coming back."""

    def __init__(
        self,
        settings: MqttSettings,
        watcher: PlayerWatcher,
        diagnostics: Diagnostics | None = None,
    ) -> None:
        """Create the bridge; call :meth:`start` to connect."""
        self._settings = settings
        self._watcher = watcher
        self._diagnostics = diagnostics or Diagnostics()
        self._task: asyncio.Task[None] | None = None
        self._client: aiomqtt.Client | None = None

    # -- topics --------------------------------------------------------------

    @property
    def base(self) -> str:
        """Root topic for everything this device publishes."""
        return self._settings.base_topic

    @property
    def availability_topic(self) -> str:
        """Topic carrying online/offline, backed by the broker's last will."""
        return f"{self.base}/availability"

    @property
    def state_topic(self) -> str:
        """Topic carrying the combined player state as JSON."""
        return f"{self.base}/state"

    @property
    def diagnostics_topic(self) -> str:
        """Topic carrying resource metrics as JSON."""
        return f"{self.base}/diagnostics"

    @property
    def command_topic(self) -> str:
        """Wildcard the bridge subscribes to for incoming commands."""
        return f"{self.base}/command/#"

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Connect and keep reconnecting in the background."""
        self._task = asyncio.create_task(self._run(), name="mqtt-bridge")

    async def stop(self) -> None:
        """Disconnect, publishing an explicit offline first."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        settings = self._settings
        will = aiomqtt.Will(self.availability_topic, OFFLINE, qos=1, retain=True)
        backoff = 2.0

        while True:
            try:
                async with aiomqtt.Client(
                    hostname=settings.host,
                    port=settings.port,
                    username=settings.username,
                    password=settings.password,
                    tls_params=aiomqtt.TLSParameters() if settings.tls else None,
                    will=will,
                    identifier=f"{settings.device_id}-nowplaying",
                ) as client:
                    self._client = client
                    _LOGGER.info("connected to MQTT broker %s:%s", settings.host, settings.port)
                    backoff = 2.0

                    await self.publish_discovery(client)
                    await client.publish(self.availability_topic, ONLINE, qos=1, retain=True)
                    await client.subscribe(self.command_topic)

                    async with asyncio.TaskGroup() as group:
                        group.create_task(self._publish_states(client))
                        group.create_task(self._publish_diagnostics(client))
                        group.create_task(self._handle_commands(client))
            except asyncio.CancelledError:
                await self._say_goodbye()
                raise
            except Exception as exc:  # noqa: BLE001 -- reconnect regardless of cause
                _LOGGER.warning("MQTT connection lost (%s); retrying in %.0fs", exc, backoff)
            finally:
                self._client = None

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _say_goodbye(self) -> None:
        """Mark ourselves offline on a clean shutdown, rather than via the will."""
        client = self._client
        if client is None:
            return
        with contextlib.suppress(Exception):
            await client.publish(self.availability_topic, OFFLINE, qos=1, retain=True)

    # -- publishing ----------------------------------------------------------

    def state_payload(self, state: State) -> dict[str, Any]:
        """Flatten the watcher's state into the shape the entities consume."""
        active = next((p for p in state.players if p.id == state.active), None)
        payload: dict[str, Any] = {
            "playing": bool(active and active.is_playing),
            "status": active.status if active else "Stopped",
            "source": active.source if active else "none",
            "player": active.identity if active else None,
            "title": active.title if active else None,
            "artist": active.artist if active else None,
            "album": active.album if active else None,
            "duration": round(active.length_us / 1e6) if active and active.length_us else None,
            "position": round(active.position_us / 1e6) if active and active.position_us else None,
            "volume": round(active.volume * 100) if active and active.volume is not None else None,
            "players": [p.identity for p in state.players],
        }
        art = active.art_url if active else None
        base = self._settings.art_base_url
        if art and art.startswith(("http://", "https://")):
            payload["art_url"] = art
        elif art and base:
            # Local file art is only reachable through our own web server.
            payload["art_url"] = f"{base.rstrip('/')}/api/art?u={quote(art, safe='')}"
        else:
            payload["art_url"] = None
        return payload

    async def _publish_states(self, client: aiomqtt.Client) -> None:
        queue = self._watcher.subscribe()
        try:
            await self._publish_state(client, self._watcher.state)
            while True:
                await self._publish_state(client, await queue.get())
        finally:
            self._watcher.unsubscribe(queue)

    async def _publish_state(self, client: aiomqtt.Client, state: State) -> None:
        payload = json.dumps(self.state_payload(state))
        await client.publish(self.state_topic, payload, qos=1, retain=True)

    async def _publish_diagnostics(self, client: aiomqtt.Client) -> None:
        while True:
            payload = json.dumps(self._diagnostics.sample().to_dict())
            await client.publish(self.diagnostics_topic, payload, qos=0, retain=True)
            await asyncio.sleep(self._settings.diagnostics_interval)

    # -- commands ------------------------------------------------------------

    async def _handle_commands(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            command = str(message.topic).rsplit("/", 1)[-1]
            payload = message.payload
            text = payload.decode(errors="replace") if isinstance(payload, bytes) else str(payload)
            try:
                await self.apply_command(command, text)
            except Exception as exc:  # noqa: BLE001 -- a bad command must not kill the bridge
                _LOGGER.warning("command %r failed: %s", command, exc)

    async def apply_command(self, command: str, payload: str) -> bool:
        """Act on one command. Separated from the loop so it can be tested."""
        if command == "volume":
            try:
                level = float(payload)
            except ValueError:
                _LOGGER.warning("ignoring non-numeric volume %r", payload)
                return False
            return await self._watcher.set_volume(level / 100)

        if command == "restart":
            _LOGGER.warning("restart requested over MQTT")
            await self._restart()
            return True

        if command in {name for name, _, _ in BUTTONS}:
            return await self._watcher.send_command(command)

        _LOGGER.warning("ignoring unknown command %r", command)
        return False

    async def _restart(self) -> None:
        """Stop supervisord so the container exits and Docker restarts it."""
        await self._say_goodbye()
        process = await asyncio.create_subprocess_exec(
            "supervisorctl",
            "shutdown",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if await process.wait() != 0:
            # No supervisord (running the web UI directly): fall back to exiting.
            _LOGGER.warning("supervisorctl shutdown failed; signalling ourselves instead")
            os.kill(os.getpid(), signal.SIGTERM)

    # -- Home Assistant discovery -------------------------------------------

    def _device(self) -> dict[str, Any]:
        return {
            "identifiers": [self._settings.device_id],
            "name": self._settings.device_name,
            "manufacturer": "sendspin-shareplay",
            "model": "AirPlay 2 / Sendspin / Bluetooth / Spotify / DLNA speaker",
            "sw_version": __version__,
        }

    def discovery_messages(self) -> list[tuple[str, dict[str, Any]]]:
        """Every discovery config to publish, as (topic, payload) pairs."""
        settings = self._settings
        device = self._device()
        prefix = settings.discovery_prefix
        uid = settings.device_id

        def common(object_id: str, name: str) -> dict[str, Any]:
            return {
                "name": name,
                "unique_id": f"{uid}_{object_id}",
                "object_id": f"{uid}_{object_id}",
                "device": device,
                "availability_topic": self.availability_topic,
                "payload_available": ONLINE,
                "payload_not_available": OFFLINE,
            }

        messages: list[tuple[str, dict[str, Any]]] = []

        def add(platform: str, object_id: str, config: dict[str, Any]) -> None:
            messages.append((f"{prefix}/{platform}/{uid}/{object_id}/config", config))

        add(
            "sensor",
            "now_playing",
            {
                **common("now_playing", "Now playing"),
                "state_topic": self.state_topic,
                "value_template": (
                    "{{ value_json.title if value_json.title else 'Nothing playing' }}"
                ),
                "json_attributes_topic": self.state_topic,
                "icon": "mdi:music-note",
            },
        )
        for key, name, icon in [
            ("artist", "Artist", "mdi:account-music"),
            ("album", "Album", "mdi:album"),
            ("source", "Source", "mdi:import"),
            ("player", "Active player", "mdi:speaker"),
        ]:
            add(
                "sensor",
                key,
                {
                    **common(key, name),
                    "state_topic": self.state_topic,
                    "value_template": f"{{{{ value_json.{key} if value_json.{key} else 'none' }}}}",
                    "icon": icon,
                },
            )

        add(
            "binary_sensor",
            "playing",
            {
                **common("playing", "Playing"),
                "state_topic": self.state_topic,
                "value_template": "{{ 'ON' if value_json.playing else 'OFF' }}",
                "device_class": "sound",
            },
        )

        add(
            "image",
            "cover_art",
            {
                **common("cover_art", "Cover art"),
                "url_topic": self.state_topic,
                "url_template": "{{ value_json.art_url }}",
            },
        )

        add(
            "number",
            "volume",
            {
                **common("volume", "Volume"),
                "state_topic": self.state_topic,
                "value_template": "{{ value_json.volume }}",
                "command_topic": f"{self.base}/command/volume",
                "min": 0,
                "max": 100,
                "step": 1,
                "unit_of_measurement": "%",
                "icon": "mdi:volume-high",
            },
        )

        for command, name, icon in BUTTONS:
            add(
                "button",
                command,
                {
                    **common(command, name),
                    "command_topic": f"{self.base}/command/{command}",
                    "payload_press": command,
                    "icon": icon,
                },
            )

        add(
            "button",
            "restart",
            {
                **common("restart", "Restart"),
                "command_topic": f"{self.base}/command/restart",
                "payload_press": "restart",
                "device_class": "restart",
                "entity_category": "config",
            },
        )

        for key, name, unit, device_class, icon in DIAGNOSTICS:
            config = {
                **common(key, name),
                "state_topic": self.diagnostics_topic,
                "value_template": f"{{{{ value_json.{key} }}}}",
                "entity_category": "diagnostic",
                "state_class": "measurement",
            }
            if unit:
                config["unit_of_measurement"] = unit
            if device_class:
                config["device_class"] = device_class
            if icon:
                config["icon"] = icon
            add("sensor", key, config)

        return messages

    async def publish_discovery(self, client: aiomqtt.Client) -> None:
        """Publish every discovery config, retained so HA sees them on restart."""
        messages = self.discovery_messages()
        for topic, config in messages:
            await client.publish(topic, json.dumps(config), qos=1, retain=True)
        _LOGGER.info(
            "published %d Home Assistant discovery configs", len(self.discovery_messages())
        )
