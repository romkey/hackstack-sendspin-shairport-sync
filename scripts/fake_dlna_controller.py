"""Act as a DLNA controller against the local renderer, for developing the UI.

Generates a tone, serves it over HTTP, then pushes it to gmediarender with
DIDL-Lite metadata exactly as a real controller (BubbleUPnP, Plex, a NAS) would.
Use it to exercise the UPnP reader in nowplaying/players.py end to end.

Run it inside the container, with ENABLE_DLNA=1:

    docker exec -d np /opt/venv/bin/python /scripts/fake_dlna_controller.py
"""

from __future__ import annotations

import argparse
import http.server
import math
import os
import socket
import socketserver
import struct
import threading
import time
import urllib.request
import wave
from xml.sax.saxutils import escape

AVTRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"


def local_ip() -> str:
    """The address libupnp will have bound the renderer to."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("1.1.1.1", 80))
        return sock.getsockname()[0]


def write_tone(path: str, seconds: int, hz: int = 440) -> None:
    """Write a stereo sine wave, so the renderer has something real to play."""
    frames = 44100 * seconds
    with wave.open(path, "w") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(44100)
        out.writeframes(
            b"".join(
                struct.pack("<hh", *((int(12000 * math.sin(2 * math.pi * hz * t / 44100)),) * 2))
                for t in range(frames)
            )
        )


def serve(directory: str, port: int) -> None:
    """Serve the tone in the background; the renderer fetches it over HTTP."""
    os.chdir(directory)
    handler = http.server.SimpleHTTPRequestHandler
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("0.0.0.0", port), handler)  # noqa: S104
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def soap(control_url: str, action: str, extra: str = "") -> int:
    """Send one AVTransport action to the renderer."""
    body = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{AVTRANSPORT}"><InstanceID>0</InstanceID>{extra}</u:{action}>'
        "</s:Body></s:Envelope>"
    )
    request = urllib.request.Request(  # noqa: S310 -- fixed local URL
        control_url,
        data=body.encode(),
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{AVTRANSPORT}#{action}"',
        },
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return response.status


def didl(title: str, artist: str, album: str, art: str, uri: str, seconds: int) -> str:
    """Build the DIDL-Lite document a controller sends with a track."""
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="1" parentID="0" restricted="1">'
        f"<dc:title>{escape(title)}</dc:title>"
        f"<upnp:artist>{escape(artist)}</upnp:artist>"
        f"<dc:creator>{escape(artist)}</dc:creator>"
        f"<upnp:album>{escape(album)}</upnp:album>"
        f"<upnp:albumArtURI>{escape(art)}</upnp:albumArtURI>"
        f'<res duration="0:00:{seconds:02d}.000" protocolInfo="http-get:*:audio/wav:*">'
        f"{escape(uri)}</res>"
        "</item></DIDL-Lite>"
    )


def main() -> None:
    """Push one track to the renderer and leave it playing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renderer-port", type=int, default=49494)
    parser.add_argument("--serve-port", type=int, default=8099)
    parser.add_argument("--title", default="Tangerine")
    parser.add_argument("--artist", default="Led Zeppelin")
    parser.add_argument("--album", default="Led Zeppelin III")
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--hold", type=int, default=0, help="stay alive this long, serving audio")
    parser.add_argument(
        "--pause",
        action="store_true",
        help="pause straight after Play, so the track stays observable without a real sound card",
    )
    args = parser.parse_args()

    directory = "/tmp/fake-dlna"  # noqa: S108 -- scratch space inside the container
    os.makedirs(directory, exist_ok=True)
    write_tone(f"{directory}/tone.wav", args.seconds)
    serve(directory, args.serve_port)

    ip = local_ip()
    uri = f"http://{ip}:{args.serve_port}/tone.wav"
    art = f"http://{ip}:{args.serve_port}/cover.jpg"
    control = f"http://{ip}:{args.renderer_port}/upnp/control/rendertransport1"

    metadata = didl(args.title, args.artist, args.album, art, uri, args.seconds)
    print(
        "SetAVTransportURI:",
        soap(
            control,
            "SetAVTransportURI",
            f"<CurrentURI>{escape(uri)}</CurrentURI>"
            f"<CurrentURIMetaData>{escape(metadata)}</CurrentURIMetaData>",
        ),
    )
    print("Play:", soap(control, "Play", "<Speed>1</Speed>"))
    if args.pause:
        print("Pause:", soap(control, "Pause"))
    print(f"playing {args.title!r} by {args.artist!r} on {control}")

    if args.hold:
        time.sleep(args.hold)


if __name__ == "__main__":
    main()
