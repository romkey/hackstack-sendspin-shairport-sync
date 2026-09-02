"""The web server's endpoints, including the art path guard."""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from nowplaying import server as server_module
from nowplaying.server import WATCHER_KEY, handle_art, handle_events, handle_health, handle_state


class StubWatcher:
    """A watcher that never touches D-Bus."""

    def __init__(self, state):
        self.state = state
        self._queues = []

    async def start(self):
        return None

    async def stop(self):
        return None

    def subscribe(self):
        import asyncio

        queue = asyncio.Queue()
        self._queues.append(queue)
        return queue

    def unsubscribe(self, queue):
        self._queues.remove(queue)


@pytest.fixture
def client_app(playing_state):
    app = web.Application()
    app[WATCHER_KEY] = StubWatcher(playing_state)
    app.router.add_get("/", server_module.handle_index)
    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/events", handle_events)
    app.router.add_get("/api/art", handle_art)
    return app


async def test_healthz_reports_ok(aiohttp_client, client_app):
    client = await aiohttp_client(client_app)
    response = await client.get("/healthz")

    assert response.status == 200
    assert (await response.json())["status"] == "ok"


async def test_state_returns_the_watcher_snapshot(aiohttp_client, client_app):
    client = await aiohttp_client(client_app)
    payload = await (await client.get("/api/state")).json()

    assert payload["active"] == "org.mpris.MediaPlayer2.ShairportSync"
    assert payload["players"][0]["title"] == "Tangerine"
    assert payload["players"][0]["source"] == "airplay"


async def test_index_is_served(aiohttp_client, client_app):
    client = await aiohttp_client(client_app)
    response = await client.get("/")

    assert response.status == 200
    assert "Now Playing" in await response.text()


async def test_events_streams_the_current_state_immediately(aiohttp_client, client_app):
    client = await aiohttp_client(client_app)
    response = await client.get("/api/events")

    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")

    line = await response.content.readuntil(b"\n\n")
    payload = json.loads(line.decode().removeprefix("data: ").strip())
    assert payload["players"][0]["title"] == "Tangerine"
    response.close()


async def test_art_requires_a_url(aiohttp_client, client_app):
    client = await aiohttp_client(client_app)
    assert (await client.get("/api/art")).status == 400


async def test_art_refuses_paths_outside_the_allowed_roots(aiohttp_client, client_app):
    # Cover art paths come from the player, so the endpoint must not serve
    # anything it is pointed at.
    client = await aiohttp_client(client_app)
    response = await client.get("/api/art", params={"u": "file:///etc/shadow"})

    assert response.status == 403


async def test_art_refuses_non_file_schemes(aiohttp_client, client_app):
    client = await aiohttp_client(client_app)
    response = await client.get("/api/art", params={"u": "http://elsewhere/art.jpg"})

    assert response.status == 400


async def test_art_serves_a_file_inside_an_allowed_root(
    aiohttp_client, client_app, tmp_path, monkeypatch
):
    art = tmp_path / "cover.png"
    art.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(server_module, "ART_ROOTS", (tmp_path.resolve(),))

    client = await aiohttp_client(client_app)
    response = await client.get("/api/art", params={"u": art.as_uri()})

    assert response.status == 200
    assert await response.read() == b"\x89PNG\r\n\x1a\n"


async def test_art_404s_for_a_missing_file_in_an_allowed_root(
    aiohttp_client, client_app, tmp_path, monkeypatch
):
    monkeypatch.setattr(server_module, "ART_ROOTS", (tmp_path.resolve(),))
    client = await aiohttp_client(client_app)
    response = await client.get("/api/art", params={"u": (tmp_path / "gone.png").as_uri()})

    assert response.status == 404
