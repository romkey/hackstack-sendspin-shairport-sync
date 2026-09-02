"""Parsing helpers in players.py, which turn three protocols into one shape."""

from __future__ import annotations

from dbus_fast import Variant
from nowplaying.players import (
    Player,
    PlayerWatcher,
    _first_str,
    _source_for,
    _unwrap,
    _upnp_duration_us,
)


def test_variants_are_unwrapped_recursively():
    value = {"a": Variant("s", "x"), "b": Variant("as", ["y", "z"])}
    assert _unwrap(value) == {"a": "x", "b": ["y", "z"]}


def test_artist_arrays_are_joined_and_empties_become_none():
    assert _first_str(["Queen", "David Bowie"]) == "Queen, David Bowie"
    assert _first_str("Solo") == "Solo"
    assert _first_str([]) is None
    assert _first_str("") is None
    assert _first_str(None) is None


def test_bus_names_map_to_sources():
    assert _source_for("org.mpris.MediaPlayer2.ShairportSync") == "airplay"
    assert _source_for("org.mpris.MediaPlayer2.sendspin") == "sendspin"
    assert _source_for("org.mpris.MediaPlayer2.spotifyd") == "spotify"
    assert _source_for("org.mpris.MediaPlayer2.vlc") == "other"


def test_upnp_durations_convert_to_microseconds():
    assert _upnp_duration_us("0:03:05.000") == 185_000_000
    assert _upnp_duration_us("1:00:00") == 3_600_000_000
    # A stopped renderer reports zero, which is absence rather than a position.
    assert _upnp_duration_us("0:00:00") is None
    assert _upnp_duration_us("NOT_IMPLEMENTED") is None
    assert _upnp_duration_us(None) is None
    assert _upnp_duration_us("nonsense") is None


def test_didl_metadata_is_parsed_including_cover_art():
    didl = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1" parentID="0" restricted="1">'
        "<dc:title>Tangerine</dc:title><upnp:artist>Led Zeppelin</upnp:artist>"
        "<upnp:album>Led Zeppelin III</upnp:album>"
        "<upnp:albumArtURI>http://nas/art.jpg</upnp:albumArtURI>"
        '<res duration="0:03:05.000">http://nas/t.mp3</res></item></DIDL-Lite>'
    )
    meta = PlayerWatcher._parse_didl(didl)

    assert meta["title"] == "Tangerine"
    assert meta["artist"] == "Led Zeppelin"
    assert meta["album"] == "Led Zeppelin III"
    assert meta["art_url"] == "http://nas/art.jpg"
    assert meta["length_us"] == 185_000_000


def test_didl_falls_back_to_dc_creator_for_the_artist():
    didl = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1"><dc:title>T</dc:title><dc:creator>Someone</dc:creator></item></DIDL-Lite>'
    )
    assert PlayerWatcher._parse_didl(didl)["artist"] == "Someone"


def test_malformed_or_empty_didl_does_not_raise():
    assert PlayerWatcher._parse_didl("") == {}
    assert PlayerWatcher._parse_didl("<not xml") == {}
    assert PlayerWatcher._parse_didl("<DIDL-Lite/>") == {}


def test_bluez_players_convert_milliseconds_and_prefer_the_device_alias():
    props = {
        "Name": "Music",
        "Status": "playing",
        "Position": 42_000,
        "Track": {"Title": "T", "Artist": "A", "Album": "B", "Duration": 185_000},
    }
    aliases = {"/org/bluez/hci0/dev_AA": "Romkey's iPhone"}
    player = PlayerWatcher._read_bluez_player("/org/bluez/hci0/dev_AA/player0", props, aliases)

    assert player.source == "bluetooth"
    assert player.identity == "Romkey's iPhone"  # not the generic AVRCP "Music"
    assert player.status == "Playing"  # BlueZ reports lowercase
    assert player.length_us == 185_000_000
    assert player.position_us == 42_000_000
    assert player.art_url is None  # BlueZ never exposes AVRCP cover art


def _player(**kwargs) -> Player:
    base = {"id": "x", "source": "airplay", "identity": "X"}
    return Player(**{**base, **kwargs})


def test_active_player_prefers_one_that_is_playing_a_known_track():
    idle = _player(id="idle")
    playing_blank = _player(id="playing_blank", status="Playing")
    playing_track = _player(id="playing_track", status="Playing", title="T")

    assert PlayerWatcher._pick_active([idle, playing_blank, playing_track]) == "playing_track"
    assert PlayerWatcher._pick_active([idle, playing_blank]) == "playing_blank"
    assert PlayerWatcher._pick_active([idle, _player(id="paused", status="Paused")]) == "paused"
    assert PlayerWatcher._pick_active([idle]) == "idle"
    assert PlayerWatcher._pick_active([]) is None
