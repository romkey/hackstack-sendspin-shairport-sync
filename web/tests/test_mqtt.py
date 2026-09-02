"""The MQTT bridge: discovery payloads, state mapping and command handling."""

from __future__ import annotations

import json

import pytest
from nowplaying.mqtt import MqttBridge, MqttSettings, slugify
from nowplaying.players import State


class FakeWatcher:
    """Stands in for PlayerWatcher, recording what the bridge asks it to do."""

    def __init__(self, state: State) -> None:
        self.state = state
        self.volumes: list[float] = []
        self.commands: list[str] = []

    def subscribe(self):  # pragma: no cover - not exercised by these tests
        raise NotImplementedError

    def unsubscribe(self, queue):  # pragma: no cover
        raise NotImplementedError

    async def set_volume(self, level: float, player_id: str | None = None) -> bool:
        self.volumes.append(level)
        return True

    async def send_command(self, command: str, player_id: str | None = None) -> bool:
        self.commands.append(command)
        return True


@pytest.fixture
def settings() -> MqttSettings:
    return MqttSettings(
        host="broker.local",
        device_name="Elab Speaker",
        device_id="elab_speaker",
        base_topic="sendspin-shareplay/elab_speaker",
        art_base_url="http://192.168.1.10:8080",
    )


@pytest.fixture
def bridge(settings, playing_state) -> MqttBridge:
    return MqttBridge(settings, FakeWatcher(playing_state))


def test_slugify_produces_topic_safe_ids():
    assert slugify("Elab Speaker") == "elab_speaker"
    assert slugify("  Kitchen/Hi-Fi!  ") == "kitchen_hi_fi"
    assert slugify("***") == "sendspin_shareplay"


def test_settings_are_only_built_when_mqtt_is_enabled():
    assert MqttSettings.from_env({"MQTT_HOST": "broker"}) is None
    assert MqttSettings.from_env({"ENABLE_MQTT": "1"}) is None

    settings = MqttSettings.from_env(
        {"ENABLE_MQTT": "1", "MQTT_HOST": "broker", "AIRPLAY_NAME": "Elab Speaker"}
    )
    assert settings is not None
    assert settings.device_id == "elab_speaker"
    assert settings.base_topic == "sendspin-shareplay/elab_speaker"


def test_state_payload_flattens_the_active_player(bridge, playing_state):
    payload = bridge.state_payload(playing_state)

    assert payload["playing"] is True
    assert payload["title"] == "Tangerine"
    assert payload["artist"] == "Led Zeppelin"
    assert payload["source"] == "airplay"
    assert payload["duration"] == 185
    assert payload["position"] == 42
    assert payload["volume"] == 50


def test_local_art_is_rewritten_to_a_url_home_assistant_can_fetch(bridge, playing_state):
    # HA cannot read file:// paths inside our container, so they must be proxied
    # through our own web server.
    url = bridge.state_payload(playing_state)["art_url"]
    assert url.startswith("http://192.168.1.10:8080/api/art?u=")
    assert "file%3A%2F%2F" in url


def test_remote_art_is_passed_through_untouched(settings, playing_state):
    playing_state.players[0].art_url = "http://nas.local/art.jpg"
    payload = MqttBridge(settings, FakeWatcher(playing_state)).state_payload(playing_state)
    assert payload["art_url"] == "http://nas.local/art.jpg"


def test_idle_state_reports_nothing_playing(bridge):
    payload = bridge.state_payload(State(players=[], active=None))

    assert payload["playing"] is False
    assert payload["source"] == "none"
    assert payload["title"] is None


def test_discovery_covers_every_entity_kind(bridge):
    topics = [topic for topic, _ in bridge.discovery_messages()]

    assert any("/sensor/elab_speaker/now_playing/config" in t for t in topics)
    assert any("/binary_sensor/elab_speaker/playing/config" in t for t in topics)
    assert any("/number/elab_speaker/volume/config" in t for t in topics)
    assert any("/button/elab_speaker/restart/config" in t for t in topics)
    assert any("/image/elab_speaker/cover_art/config" in t for t in topics)
    assert any("/sensor/elab_speaker/cpu_temperature_c/config" in t for t in topics)


def test_every_discovery_payload_is_valid_and_self_consistent(bridge):
    for topic, config in bridge.discovery_messages():
        assert topic.startswith("homeassistant/")
        json.dumps(config)  # must be serialisable

        assert config["unique_id"].startswith("elab_speaker_")
        assert config["availability_topic"] == bridge.availability_topic
        assert config["device"]["identifiers"] == ["elab_speaker"]
        # Anything with a command topic must sit under our command prefix, or
        # the bridge will never see the press.
        if "command_topic" in config:
            assert config["command_topic"].startswith(f"{bridge.base}/command/")


def test_unique_ids_do_not_collide(bridge):
    unique_ids = [config["unique_id"] for _, config in bridge.discovery_messages()]
    assert len(unique_ids) == len(set(unique_ids))


@pytest.mark.asyncio
async def test_volume_command_is_scaled_to_a_fraction(bridge):
    assert await bridge.apply_command("volume", "75") is True
    assert bridge._watcher.volumes == [0.75]


@pytest.mark.asyncio
async def test_volume_command_ignores_rubbish(bridge):
    assert await bridge.apply_command("volume", "loud") is False
    assert bridge._watcher.volumes == []


@pytest.mark.asyncio
async def test_transport_commands_are_forwarded(bridge):
    for command in ("play_pause", "next", "previous", "stop"):
        assert await bridge.apply_command(command, command) is True
    assert bridge._watcher.commands == ["play_pause", "next", "previous", "stop"]


@pytest.mark.asyncio
async def test_unknown_commands_are_refused(bridge):
    assert await bridge.apply_command("self_destruct", "now") is False
    assert bridge._watcher.commands == []
