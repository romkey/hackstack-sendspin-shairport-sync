# hackstack-sendspin-shairport-sync

**One container that turns a Raspberry Pi into both a [Sendspin](https://github.com/Sendspin/sendspin-cli) player and an [AirPlay 2](https://github.com/mikebrady/shairport-sync) speaker — with a single web page showing whatever is playing.**

[![build](https://github.com/romkey/hackstack-sendspin-shairport-sync/actions/workflows/build.yml/badge.svg)](https://github.com/romkey/hackstack-sendspin-shairport-sync/actions/workflows/build.yml)
[![lint](https://github.com/romkey/hackstack-sendspin-shairport-sync/actions/workflows/lint.yml/badge.svg)](https://github.com/romkey/hackstack-sendspin-shairport-sync/actions/workflows/lint.yml)
[![upstream-watch](https://github.com/romkey/hackstack-sendspin-shairport-sync/actions/workflows/upstream-watch.yml/badge.svg)](https://github.com/romkey/hackstack-sendspin-shairport-sync/actions/workflows/upstream-watch.yml)
[![ghcr.io](https://img.shields.io/badge/ghcr.io-hackstack--sendspin--shairport--sync-2496ed?logo=docker&logoColor=white)](https://github.com/romkey/hackstack-sendspin-shairport-sync/pkgs/container/hackstack-sendspin-shairport-sync)
[![license](https://img.shields.io/github/license/romkey/hackstack-sendspin-shairport-sync)](LICENSE)

[![shairport-sync](https://img.shields.io/github/v/release/mikebrady/shairport-sync?label=shairport-sync&color=informational)](https://github.com/mikebrady/shairport-sync/releases)
[![nqptp](https://img.shields.io/github/v/release/mikebrady/nqptp?label=nqptp&color=informational)](https://github.com/mikebrady/nqptp/releases)
[![sendspin](https://img.shields.io/pypi/v/sendspin?label=sendspin&color=informational)](https://pypi.org/project/sendspin/)

The upstream badges show the *latest released* versions. The versions this image is
pinned to live in [`versions.env`](versions.env); a daily job opens a pull request
whenever upstream moves ahead of them.

---

> ### 🤖 Built with AI assistance
>
> This repository — the Dockerfile, the entrypoint and service scripts, the web UI,
> the GitHub Actions workflows and this README — was generated with
> [Claude Code](https://claude.com/claude-code) and then reviewed and tested by a
> human. The image builds and the container was exercised end to end, but treat it
> the way you would any code you didn't write yourself: read it before you run it.
>
> The upstream software it packages — Shairport Sync, nqptp and Sendspin — is not
> AI-generated. See [Credits](#credits).

## What you get

- **AirPlay 2** via Shairport Sync + nqptp — play to the Pi from iPhone, iPad, Mac, or HomePod groups.
- **Sendspin** via the official `sendspin` daemon — the open multi-room protocol used by Music Assistant and Home Assistant.
- **Bluetooth A2DP** (optional, off by default) — pair a phone and play to the Pi directly, as a third source.
- **One web UI** at `http://<pi>:8080` that shows cover art, title, artist, album and progress for *whichever* source is playing.
- Both players **sharing the same sound card** through ALSA `dmix`, so you don't have to pick one.
- Multi-arch images (`linux/amd64`, `linux/arm64`) published to GitHub Container Registry on every push, tag, and upstream release.

## How the unified UI works

Shairport Sync and the Sendspin daemon both speak **MPRIS**, the standard Linux media-player
D-Bus interface. The container runs a private D-Bus session bus, points both players at it,
and the web UI is simply an MPRIS observer:

```
                 ┌───────────────────── container ──────────────────────┐
  iPhone ──────► │  shairport-sync ──┐                                  │
  (AirPlay 2)    │  + nqptp          │                                  │
                 │                   ├─► session bus (MPRIS) ─┐         │
  Music          │  sendspin daemon ─┘                        │         │
  Assistant ───► │        │                                   ▼         │
  (Sendspin)     │        │                        nowplaying web UI ───┼──► :8080
                 │        │                                   ▲         │
  Phone ───────► │  bluetoothd + bluealsa ─► system bus (AVRCP)         │
  (Bluetooth)    │        │                                             │
                 │        ▼                                             │
                 │   ALSA dmix ────────────────────────────────────────►│──► 3.5mm jack / DAC
                 └──────────────────────────────────────────────────────┘
```

Nothing is hard-coded to particular players: any process exporting
`org.mpris.MediaPlayer2.*` on the session bus shows up in the UI, and so does any
`org.bluez.MediaPlayer1` on the system bus.

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

Then pull the repo (or just [`docker-compose.prod.yml`](docker-compose.prod.yml) and
[`.env.example`](.env.example)), set `ALSA_PCM` and the names, and start it:

```bash
git clone https://github.com/romkey/hackstack-sendspin-shairport-sync.git
cd hackstack-sendspin-shairport-sync
cp .env.example .env
$EDITOR .env
docker compose -f docker-compose.prod.yml up -d
```

That runs the published image from GHCR. Open `http://<pi-address>:8080`, and the
speaker appears in AirPlay pickers and in Music Assistant.

To update later:

```bash
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

There are two compose files: [`docker-compose.prod.yml`](docker-compose.prod.yml) pulls
the published image and reads everything from `.env`, while
[`docker-compose.yml`](docker-compose.yml) builds from this checkout with the settings
inline — use that one when you are changing the image itself.

> **Host networking is required.** AirPlay 2 needs mDNS on the LAN and nqptp needs
> UDP ports 319/320. Bridge networking will not work.

## Configuration

Everything is environment variables. Copy [`.env.example`](.env.example) to `.env` —
it lists every variable below with notes — and mount `/config` for persistence and
file-level overrides.

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
| `ENABLE_BLUETOOTH` | `0` | Set `1` for the Bluetooth A2DP sink — see below |
| `BLUETOOTH_NAME` | `AIRPLAY_NAME` | Name shown when pairing |
| `BLUETOOTH_ADAPTER` | `hci0` | Which adapter to use |
| `BLUETOOTH_AUDIO_DEVICE` | `default` | ALSA device Bluetooth audio plays to |
| `BLUETOOTH_DISCOVERABLE` | `1` | Set `0` to stop advertising once paired |
| `WEB_PORT` | `8080` | Web UI port |
| `LOG_LEVEL` | `info` | `debug` for much noisier logs |
| `EXTRA_SHAIRPORT_ARGS` | *unset* | Appended to the `shairport-sync` command line |
| `EXTRA_SENDSPIN_ARGS` | *unset* | Appended to the `sendspin daemon` command line |
| `TZ` | *unset* | Timezone for log timestamps |

### Config file overrides

When the environment variables aren't enough, drop a config file into the mounted
`config/` directory and the entrypoint uses it verbatim instead of generating one.
Annotated starting points ship in [`config/`](config/):

- `config/shairport-sync.conf` — full [Shairport Sync configuration](https://github.com/mikebrady/shairport-sync/blob/master/scripts/shairport-sync.conf).
  If you write your own, keep `mpris_service_bus = "session";` or the web UI will lose AirPlay metadata.
- `config/asound.conf` — your own ALSA routing, including softvol and bit-perfect variants.

```bash
cp config/shairport-sync.conf.example config/shairport-sync.conf
docker compose -f docker-compose.prod.yml restart
```

Sendspin's own persistent settings live in `config/sendspin/`. See
[`config/README.md`](config/README.md) for the details, including which `.env`
variables a config file overrides.

## Sharing one sound card

A sound card can normally only be opened by one program at a time, so by default the
container puts an ALSA `dmix` device in front of it and points both players at it.
That means AirPlay and Sendspin can both be connected at once — and if two sources
play simultaneously you will hear both, mixed.

`dmix` fixes the output at 16-bit stereo at `ALSA_RATE`, and resamples anything else.
For bit-perfect output to a good DAC, set `AUDIO_SHARING=exclusive` — then only one
player can hold the card, and the other will fail to start playback until it's free.

## Bluetooth (optional)

Off by default. Turning it on adds a third source: a phone pairs with the Pi and
plays straight to it over A2DP, mixed into the same output as AirPlay and Sendspin,
and showing up in the same web UI via AVRCP metadata.

> **Not verified on hardware.** Everything else here was tested end to end in a
> container. Bluetooth could not be — Docker has no radio to give it, so the
> BlueZ reader in the web UI was tested against a stand-in service
> ([`scripts/fake_bluez.py`](scripts/fake_bluez.py)) and the daemons were only
> checked as far as "they start and take their D-Bus names". The pairing and
> audio path itself is unproven. Treat this feature as beta.

It needs three things beyond `ENABLE_BLUETOOTH=1`:

1. **`NET_ADMIN`** on the container — uncomment it under `cap_add` in the compose file.
2. **Host networking**, which you already have. Bluetooth adapters belong to a network
   namespace, so the container sees `hci0` only because it shares the host's.
3. **The host's Bluetooth stack stopped.** Only one `bluetoothd` can own an adapter,
   and it has to be the container's:

   ```bash
   sudo systemctl disable --now bluetooth
   ```

That third point is the real cost: the Pi then has no Bluetooth of its own — no BT
keyboards, no host pairing. If you need Bluetooth on the host for anything else,
leave this off.

**Pairing is automatic.** There is no screen or keypad on a headless Pi, so the
container runs an agent that accepts every pairing request and trusts the device
afterwards so it can reconnect on its own. That means anyone in radio range can pair
while the Pi is discoverable. Once your own devices are paired, set
`BLUETOOTH_DISCOVERABLE=0` to stop advertising. Pairing keys are stored in
`config/bluetooth/`, so they survive a rebuild.

**What to expect:**

- **SBC only**, so quality sits below AirPlay and Sendspin.
- **No cover art.** AVRCP 1.6 can carry it but BlueZ does not expose it, so Bluetooth
  tracks show the placeholder, same as Sendspin.
- **2.4 GHz contention is real.** The Pi's Bluetooth and WiFi share silicon and an
  antenna path. On 2.4 GHz WiFi you should expect audible dropouts during Bluetooth
  playback. On 5 GHz or Ethernet it is largely a non-issue.

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
- **Bluetooth takes over the adapter.** Enabling it means the host cannot use Bluetooth
  at all, and it is the one feature here not verified on real hardware.

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

**Bluetooth won't start.** `bluetoothd` logging `Failed to access management
interface` means it cannot reach the adapter: check `NET_ADMIN` is granted, that the
host's own `bluetooth` service is stopped, and that `hciconfig -a` on the host shows
the adapter. Nothing pairs? Watch `docker logs` for the `bt-agent` lines — it logs
every authorisation it accepts.

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

[`scripts/fake_bluez.py`](scripts/fake_bluez.py) does the same for the Bluetooth side,
standing in for BlueZ on the system bus so the AVRCP reader can be exercised without a
radio:

```bash
docker exec -d np /opt/venv/bin/python /scripts/fake_bluez.py --alias "Test Phone"
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

## Versioning

Three version numbers matter here, and they move independently:

| What | Where it lives | How it moves |
| --- | --- | --- |
| Image release | git tags (`v0.2.0`) | Tagging pushes `0.2.0`, `0.2` and `latest` to GHCR |
| Bundled upstreams | [`versions.env`](versions.env) | The daily watcher opens a bump PR |
| Web UI package | [`web/pyproject.toml`](web/pyproject.toml) | Bumped by hand; served at `/healthz` |

Every push to `main` also publishes `latest` and a `sha-<short>` tag, so you don't have
to wait for a release to run the newest build. Cut a release with:

```bash
git tag -a v0.2.0 -m "v0.2.0" && git push origin v0.2.0
```

## Credits

This project is glue. The real work belongs to:

- [Shairport Sync](https://github.com/mikebrady/shairport-sync) and [nqptp](https://github.com/mikebrady/nqptp) by Mike Brady
- [Sendspin](https://github.com/Sendspin/sendspin-cli) (formerly Resonate), from the Open Home Foundation / Music Assistant community

Licensed under the [MIT License](LICENSE). Shairport Sync, nqptp and Sendspin carry their
own licenses.
