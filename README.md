# sendspin-shareplay

**One container that turns a Raspberry Pi into both a [Sendspin](https://github.com/Sendspin/sendspin-cli) player and an [AirPlay 2](https://github.com/mikebrady/shairport-sync) speaker — with a single web page showing whatever is playing.**

[![build](https://github.com/romkey/sendspin-shareplay/actions/workflows/build.yml/badge.svg)](https://github.com/romkey/sendspin-shareplay/actions/workflows/build.yml)
[![lint](https://github.com/romkey/sendspin-shareplay/actions/workflows/lint.yml/badge.svg)](https://github.com/romkey/sendspin-shareplay/actions/workflows/lint.yml)
[![upstream-watch](https://github.com/romkey/sendspin-shareplay/actions/workflows/upstream-watch.yml/badge.svg)](https://github.com/romkey/sendspin-shareplay/actions/workflows/upstream-watch.yml)
[![ghcr.io](https://img.shields.io/badge/ghcr.io-sendspin--shareplay-2496ed?logo=docker&logoColor=white)](https://github.com/romkey/sendspin-shareplay/pkgs/container/sendspin-shareplay)
[![license](https://img.shields.io/github/license/romkey/sendspin-shareplay)](LICENSE)

[![shairport-sync](https://img.shields.io/github/v/release/mikebrady/shairport-sync?label=shairport-sync&color=informational)](https://github.com/mikebrady/shairport-sync/releases)
[![nqptp](https://img.shields.io/github/v/release/mikebrady/nqptp?label=nqptp&color=informational)](https://github.com/mikebrady/nqptp/releases)
[![sendspin](https://img.shields.io/pypi/v/sendspin?label=sendspin&color=informational)](https://pypi.org/project/sendspin/)

The upstream badges show the *latest released* versions. The versions this image is
pinned to live in [`versions.env`](versions.env); a daily job opens a pull request
whenever upstream moves ahead of them.

---

## What you get

- **AirPlay 2** via Shairport Sync + nqptp — play to the Pi from iPhone, iPad, Mac, or HomePod groups.
- **Sendspin** via the official `sendspin` daemon — the open multi-room protocol used by Music Assistant and Home Assistant.
- **One web UI** at `http://<pi>:8080` that shows cover art, title, artist, album and progress for *whichever* source is playing.
- Both players **sharing the same sound card** through ALSA `dmix`, so you don't have to pick one.
- Multi-arch images (`linux/amd64`, `linux/arm64`) published to GitHub Container Registry on every push, tag, and upstream release.

## How the unified UI works

Shairport Sync and the Sendspin daemon both speak **MPRIS**, the standard Linux media-player
D-Bus interface. The container runs a private D-Bus session bus, points both players at it,
and the web UI is simply an MPRIS observer:

```
                 ┌──────────────────── container ─────────────────────┐
  iPhone ──────► │  shairport-sync ──┐                                │
  (AirPlay 2)    │  + nqptp          │                                │
                 │                   ├─► D-Bus session bus (MPRIS)    │
  Music          │  sendspin daemon ─┘         │                      │
  Assistant ───► │        │                    ▼                      │
  (Sendspin)     │        │            nowplaying web UI ─────────────┼──► :8080
                 │        ▼                                           │
                 │   ALSA dmix ──────────────────────────────────────►│──► 3.5mm jack / DAC
                 └────────────────────────────────────────────────────┘
```

Nothing is hard-coded to the two players: any process on that bus that exports
`org.mpris.MediaPlayer2.*` shows up in the UI.

## Quick start on a Raspberry Pi

Requires a 64-bit OS (Raspberry Pi OS Bookworm/Trixie 64-bit, or Ubuntu) on a Pi 3 or
newer. 32-bit and ARMv6 (Pi Zero W, Pi 1) are **not** supported — see
[Limitations](#limitations).

```bash
sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker "$USER"   # log out and back in
```

Find your output device:

```bash
aplay -l
```

The 3.5&nbsp;mm headphone jack is usually `hw:Headphones,0`; a HAT DAC is typically
`hw:sndrpihifiberry,0` or similar.

Then grab [`docker-compose.yml`](docker-compose.yml), set `ALSA_PCM` and the names, and:

```bash
docker compose up -d
```

Open `http://<pi-address>:8080`, and the speaker appears in AirPlay pickers and in
Music Assistant.

> **Host networking is required.** AirPlay 2 needs mDNS on the LAN and nqptp needs
> UDP ports 319/320. Bridge networking will not work.

## Configuration

Everything is environment variables; mount `/config` for persistence and overrides.

| Variable | Default | What it does |
| --- | --- | --- |
| `AIRPLAY_NAME` | container hostname | Name shown in AirPlay pickers |
| `SENDSPIN_NAME` | container hostname | Name shown in Music Assistant |
| `ENABLE_AIRPLAY` | `1` | Set `0` to run Sendspin only |
| `ENABLE_SENDSPIN` | `1` | Set `0` to run AirPlay only |
| `ENABLE_WEB` | `1` | Set `0` to drop the web UI |
| `AIRPLAY_MODE` | `airplay2` | `airplay2`, `classic` (AirPlay 1), or `auto` |
| `AUDIO_SHARING` | `dmix` | `dmix` (share the card), `exclusive`, or `none` |
| `ALSA_PCM` | `hw:0,0` | The real output device dmix feeds |
| `ALSA_RATE` | `44100` | dmix mix rate |
| `ALSA_MIXER_CONTROL` | *unset* | ALSA mixer control for hardware volume, e.g. `PCM` |
| `SHAIRPORT_OUTPUT_DEVICE` | `default` | ALSA device Shairport Sync opens |
| `SENDSPIN_AUDIO_DEVICE` | `default` | Audio device the Sendspin daemon opens |
| `SENDSPIN_URL` | *unset* | Pin a server (`ws://host:8927/sendspin`) instead of using mDNS |
| `SENDSPIN_AUDIO_FORMAT` | *unset* | e.g. `flac:48000:24:2` |
| `SENDSPIN_HARDWARE_VOLUME` | `false` | dmix has no hardware mixer, so software volume by default |
| `SENDSPIN_INTERFACE` | *unset* | Bind Sendspin to one network interface |
| `WEB_PORT` | `8080` | Web UI port |
| `LOG_LEVEL` | `info` | `debug` for much noisier logs |
| `EXTRA_SHAIRPORT_ARGS` | *unset* | Appended to the `shairport-sync` command line |
| `EXTRA_SENDSPIN_ARGS` | *unset* | Appended to the `sendspin daemon` command line |
| `TZ` | *unset* | Timezone for log timestamps |

### Config file overrides

Drop either of these into the mounted `/config` volume and the entrypoint will use it
verbatim instead of generating one:

- `/config/shairport-sync.conf` — full [Shairport Sync configuration](https://github.com/mikebrady/shairport-sync/blob/master/scripts/shairport-sync.conf).
  If you write your own, keep `mpris_service_bus = "session";` or the web UI will lose AirPlay metadata.
- `/config/asound.conf` — your own ALSA routing.

Sendspin's own persistent settings live in `/config/sendspin/`.

## Sharing one sound card

A sound card can normally only be opened by one program at a time, so by default the
container puts an ALSA `dmix` device in front of it and points both players at it.
That means AirPlay and Sendspin can both be connected at once — and if two sources
play simultaneously you will hear both, mixed.

`dmix` fixes the output at 16-bit stereo at `ALSA_RATE`, and resamples anything else.
For bit-perfect output to a good DAC, set `AUDIO_SHARING=exclusive` — then only one
player can hold the card, and the other will fail to start playback until it's free.

## Web UI

- `/` — the now-playing page (server-sent events; no polling from the browser)
- `/api/state` — current state as JSON
- `/api/events` — the SSE stream
- `/healthz` — used by the container health check

Shairport Sync publishes MPRIS as soon as it starts, so it is always listed. The
Sendspin daemon only publishes MPRIS while it is connected to a Sendspin server, so
it appears in the UI once Music Assistant (or another server) has claimed it.

Cover art comes from AirPlay sources. Sendspin's MPRIS interface publishes title,
artist, album, duration and position but **not** artwork, so Sendspin tracks show
the placeholder art. Shairport Sync does not report playback position over MPRIS,
so the AirPlay progress bar shows track length with elapsed time interpolated from
the last reported position.

## Limitations

- **64-bit only.** The `sendspin` package ships wheels for `x86_64` and `aarch64`; there
  are no ARMv6/ARMv7 builds, so a Pi Zero W or a 32-bit OS won't work. Use a 64-bit OS on
  a Pi 3 or later.
- **AirPlay 2 needs the real network.** `network_mode: host` plus `cap_add: SYS_NICE`.
- **AirPlay 2 wants a Pi 3 or better.** A Pi 2 or Zero 2 W can manage AirPlay 1
  (`AIRPLAY_MODE=classic`) more comfortably.
- Shairport Sync cannot run AirPlay 2 inside a VM whose audio goes through ALSA/PulseAudio
  on the host — the timing requirements aren't met.

## Troubleshooting

```bash
docker logs -f sendspin-shareplay          # everything, prefixed by program name
docker exec sendspin-shareplay aplay -l    # what ALSA sees
docker exec sendspin-shareplay supervisorctl status

# which players are on the MPRIS bus right now
docker exec sendspin-shareplay dbus-send --session --print-reply \
  --dest=org.freedesktop.DBus /org/freedesktop/DBus \
  org.freedesktop.DBus.ListNames | grep mpris
```

**No AirPlay speaker appears.** Confirm host networking, then check that nothing else on
the Pi already owns ports 319/320 (`sudo ss -lunp | grep -E '319|320'`) — a host `ptpd`
or another Shairport Sync install will block nqptp. Also stop the host's `avahi-daemon`
if it conflicts, or set `ENABLE_AIRPLAY=0` to isolate the problem.

**Sound card busy / no audio.** `AUDIO_SHARING=dmix` is the fix for two players wanting
one card; if you set `exclusive`, expect exactly one to work at a time. Check `ALSA_PCM`
matches `aplay -l`.

**Web UI shows "no players detected".** Both players publish MPRIS only once they are
running; check `supervisorctl status` and the logs for why one exited.

## Building locally

```bash
docker build -t sendspin-shareplay:dev .
./scripts/smoke-test.sh sendspin-shareplay:dev
```

To work on the web UI without any audio hardware, run the container with the players
off and publish a fake MPRIS player onto its bus:

```bash
docker run -d --name np -p 8080:8080 \
  -e ENABLE_AIRPLAY=0 -e ENABLE_SENDSPIN=0 -e AUDIO_SHARING=none \
  -v "$PWD/scripts:/scripts:ro" sendspin-shareplay:dev

docker exec -d np env DBUS_SESSION_BUS_ADDRESS=unix:path=/run/dbus/session_bus_socket \
  /opt/venv/bin/python /scripts/fake_mpris.py --name ShairportSync --title "Purple Rain"
```

Override the pinned upstreams:

```bash
docker build \
  --build-arg SHAIRPORT_SYNC_VERSION=5.2.3 \
  --build-arg NQPTP_VERSION=1.2.8 \
  --build-arg SENDSPIN_VERSION=7.5.0 \
  -t sendspin-shareplay:dev .
```

## Continuous integration

| Workflow | Trigger | What it does |
| --- | --- | --- |
| [`build.yml`](.github/workflows/build.yml) | push to `main`, tags, PRs, weekly cron, `repository_dispatch` | Builds `linux/amd64` + `linux/arm64`, pushes to GHCR with build provenance, then smoke-tests the pushed image |
| [`upstream-watch.yml`](.github/workflows/upstream-watch.yml) | daily cron | Checks shairport-sync, nqptp and sendspin releases; opens a PR bumping `versions.env` when one moves |
| [`lint.yml`](.github/workflows/lint.yml) | push, PR | hadolint, shellcheck, ruff |

Pull requests build for `amd64` only and don't push, so they stay fast. Merging an
upstream-bump PR rebuilds and republishes automatically. The weekly cron rebuild picks
up Debian security updates even when nothing in the repo changed.

## Credits

This project is glue. The real work belongs to:

- [Shairport Sync](https://github.com/mikebrady/shairport-sync) and [nqptp](https://github.com/mikebrady/nqptp) by Mike Brady
- [Sendspin](https://github.com/Sendspin/sendspin-cli) (formerly Resonate), from the Open Home Foundation / Music Assistant community

Licensed under the [MIT License](LICENSE). Shairport Sync, nqptp and Sendspin carry their
own licenses.
