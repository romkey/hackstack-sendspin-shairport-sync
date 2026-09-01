"""aiohttp server for the unified now-playing UI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from aiohttp import web

from nowplaying import __version__
from nowplaying.players import PlayerWatcher

_LOGGER = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Cover art arrives as a local path from the player itself; only serve files that
# live under a directory a player is expected to write art into.
ART_ROOTS = tuple(
    Path(p).resolve()
    for p in os.environ.get(
        "ART_ROOTS",
        "/var/lib/shairport-sync/coverart:/tmp/shairport-sync:/config",
    ).split(":")
    if p
)

WATCHER_KEY = web.AppKey("watcher", PlayerWatcher)


async def handle_index(request: web.Request) -> web.FileResponse:
    """Serve the single-page UI."""
    return web.FileResponse(STATIC_DIR / "index.html")


async def handle_state(request: web.Request) -> web.Response:
    """Return the current combined player state."""
    return web.json_response(request.app[WATCHER_KEY].state.to_dict())


async def handle_health(request: web.Request) -> web.Response:
    """Liveness probe used by the container HEALTHCHECK."""
    return web.json_response({"status": "ok", "version": __version__})


async def handle_events(request: web.Request) -> web.StreamResponse:
    """Push state changes to the browser over server-sent events."""
    response = web.StreamResponse(
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
    await response.prepare(request)

    watcher = request.app[WATCHER_KEY]
    queue = watcher.subscribe()
    try:
        await _send_event(response, watcher.state.to_dict())
        while True:
            try:
                state = await asyncio.wait_for(queue.get(), timeout=20)
            except TimeoutError:
                await response.write(b": keepalive\n\n")  # keep proxies from closing us
                continue
            await _send_event(response, state.to_dict())
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        watcher.unsubscribe(queue)
    return response


async def _send_event(response: web.StreamResponse, payload: dict) -> None:
    await response.write(f"data: {json.dumps(payload)}\n\n".encode())


async def handle_art(request: web.Request) -> web.StreamResponse:
    """Serve cover art that a player cached on the local filesystem."""
    url = request.query.get("u")
    if not url:
        raise web.HTTPBadRequest(text="missing 'u'")

    parsed = urlparse(url)
    if parsed.scheme not in ("file", ""):
        raise web.HTTPBadRequest(text="only file:// art is served locally")

    path = Path(unquote(parsed.path)).resolve()
    if not any(path.is_relative_to(root) for root in ART_ROOTS):
        _LOGGER.warning("refusing to serve art outside the allowed roots: %s", path)
        raise web.HTTPForbidden(text="art path not allowed")
    if not path.is_file():
        raise web.HTTPNotFound(text="art not found")

    content_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return web.FileResponse(
        path, headers={"Content-Type": content_type, "Cache-Control": "public, max-age=3600"}
    )


async def _start_watcher(app: web.Application) -> None:
    await app[WATCHER_KEY].start()


async def _stop_watcher(app: web.Application) -> None:
    await app[WATCHER_KEY].stop()


def create_app(
    poll_interval: float = 1.0,
    dlna_port: int | None = None,
    dlna_url: str | None = None,
) -> web.Application:
    """Build the aiohttp application."""
    app = web.Application()
    app[WATCHER_KEY] = PlayerWatcher(
        poll_interval=poll_interval, dlna_port=dlna_port, dlna_url=dlna_url
    )

    app.router.add_get("/", handle_index)
    app.router.add_get("/healthz", handle_health)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/events", handle_events)
    app.router.add_get("/api/art", handle_art)
    app.router.add_static("/static", STATIC_DIR, name="static")

    app.on_startup.append(_start_watcher)
    app.on_cleanup.append(_stop_watcher)
    return app


def run(
    host: str,
    port: int,
    poll_interval: float = 1.0,
    dlna_port: int | None = None,
    dlna_url: str | None = None,
) -> None:
    """Run the server until interrupted."""
    with contextlib.suppress(KeyboardInterrupt):
        web.run_app(
            create_app(poll_interval, dlna_port, dlna_url), host=host, port=port, print=None
        )
