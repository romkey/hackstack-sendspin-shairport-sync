"""Shared fixtures."""

from __future__ import annotations

import pytest
from nowplaying.players import Player, State


@pytest.fixture
def playing_state() -> State:
    """A state with one AirPlay player mid-track."""
    player = Player(
        id="org.mpris.MediaPlayer2.ShairportSync",
        source="airplay",
        identity="Shairport Sync",
        status="Playing",
        title="Tangerine",
        artist="Led Zeppelin",
        album="Led Zeppelin III",
        art_url="file:///var/lib/shairport-sync/coverart/a.jpg",
        length_us=185_000_000,
        position_us=42_000_000,
        volume=0.5,
    )
    return State(players=[player], active=player.id)
