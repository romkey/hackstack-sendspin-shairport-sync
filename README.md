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
- **Bluetooth A2DP** (optional, off by default) — pair a phone and play to the Pi directly.
- **Spotify Connect** (optional, off by default) — the Pi appears in the Spotify app's device picker.
- **DLNA/UPnP renderer** (optional, off by default) — a "play to" target for Android apps, BubbleUPnP, Plex and most NAS media servers.
- **One web UI** at `http://<pi>:8080` that shows cover art, title, artist, album and progress for *whichever* source is playing.
- Both players **sharing the same sound card** through ALSA `dmix`, so you don't have to pick one.
- Multi-arch images (`linux/amd64`, `linux/arm64`) published to GitHub Container Registry on every push, tag, and upstream release.

## How the unified UI works

Shairport Sync and the Sendspin daemon both speak **MPRIS**, the standard Linux media-player
D-Bus interface. The container runs a private D-Bus session bus, points both players at it,
and the web UI is simply an MPRIS observer:

```
                  ┌──────────────────── container ─────────────────────┐
  iPhone ───────► │  shairport-sync ─┐                                 │
  (AirPlay 2)     │  + nqptp         │                                 │
                  │                  │                                 │
  Music           │  sendspin ───────┼─► session bus (MPRIS) ─┐        │
  Assistant ────► │                  │                        │        │
                  │  spotifyd ───────┘                        │        │
  Spotify app ──► │        │                                  ▼        │
                  │        │                      nowplaying web UI ───┼──► :8080
  Phone ────────► │  bluetoothd + bluealsa ─► system bus ──►   ▲       │
  (Bluetooth)     │        │                       (AVRCP)     │       │
                  │        │                                   │       │
  BubbleUPnP ───► │  gmediarender ──────────► HTTP/SOAP ───────┘       │
  (DLNA)          │        │                    (AVTransport)          │
                  │        ▼                                           │
                  │   ALSA dmix ──────────────────────────────────────►│──► 3.5mm jack / DAC
                  └────────────────────────────────────────────────────┘
```

Three of the five speak MPRIS, so they need no special handling — anything exporting
`org.mpris.MediaPlayer2.*` on the session bus shows up in the UI automatically. The
other two are adapted: BlueZ publishes AVRCP metadata as `org.bluez.MediaPlayer1` on
the system bus, and gmediarender has no bus interface at all, so the UI queries its
UPnP `AVTransport` service over SOAP exactly as any DLNA controller would.

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
| `ENABLE_SPOTIFY` | `0` | Set `1` for Spotify Connect |
| `SPOTIFY_NAME` | `AIRPLAY_NAME` | Name in the Spotify device picker |
| `SPOTIFY_AUDIO_DEVICE` | `default` | ALSA device Spotify plays to |
| `SPOTIFY_INITIAL_VOLUME` | *unset* | Starting volume, 0–100 |
| `ENABLE_DLNA` | `0` | Set `1` for the DLNA/UPnP renderer |
| `DLNA_NAME` | `AIRPLAY_NAME` | Name shown in DLNA controllers |
| `DLNA_PORT` | `49494` | UPnP HTTP port |
| `DLNA_AUDIO_DEVICE` | `default` | ALSA device DLNA plays to |
| `DLNA_AUDIO_ONLY` | `1` | Set `0` to also advertise video |
| `EXTRA_SPOTIFYD_ARGS` | *unset* | Appended to the `spotifyd` command line |
| `EXTRA_GMEDIARENDER_ARGS` | *unset* | Appended to the `gmediarender` command line |
| `AVAHI_MODE` | `auto` | `auto`, `host` or `container` — see [mDNS](#mdns-and-the-hosts-avahi) |
| `AVAHI_INTERFACES` | real interfaces | Comma-separated interfaces Avahi may announce on |
| `AVAHI_HOST_NAME` | system hostname | Override the `.local` name in container mode |
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

## mDNS and the host's Avahi

AirPlay, Spotify Connect and DLNA all announce themselves over mDNS, and with host
networking there is only one network stack to announce on. **Raspberry Pi OS runs
`avahi-daemon` by default**, so if the container starts a second one the two fight over
the machine's `.local` name and rename each other in a loop:

```
Host name conflict, retrying with heavy-metal-5
Host name conflict, retrying with heavy-metal-6
```

The compose files therefore mount the host's Avahi and D-Bus sockets:

```yaml
volumes:
  - /var/run/dbus:/var/run/dbus
  - /var/run/avahi-daemon:/var/run/avahi-daemon
```

With both present the container detects them and uses the host's daemon instead of
starting its own — services register against the host's Avahi and the name stays
`heavy-metal.local`. `AVAHI_MODE` controls this:

| Value | Behaviour |
| --- | --- |
| `auto` (default) | Use the host's daemon if it actually answers on the system bus, otherwise run one inside the container |
| `host` | Always use the host's daemon; fails to advertise if the sockets are missing |
| `container` | Always run our own — correct only when the host has no `avahi-daemon` |

Detection asks the system bus whether `org.freedesktop.Avahi` has an owner, rather than
just checking that the sockets exist — a mounted socket with nothing behind it is exactly
the case that made Shairport Sync exit with `Could not establish mDNS advertisement!` in
a restart loop. When the sockets are mounted but Avahi does not answer, the container
falls back to running its own under a distinct `<hostname>-shareplay` name, so it still
advertises without colliding with the host.

**Docker bridge addresses.** In container mode Avahi would otherwise announce every
interface it can see, including `docker0`, `br-*` and `veth*`. An AirPlay client that
picks `172.17.0.1` out of that list simply fails to connect. The entrypoint therefore
restricts announcements to the machine's real interfaces — everything except loopback
and the ones Docker creates — so a Pi on both Ethernet and Wi-Fi advertises on
`eth0,wlan0` and nothing else. Override with `AVAHI_INTERFACES=wlan0` to narrow it
further. If the host's own Avahi has the
same problem, fix it in the host's `/etc/avahi/avahi-daemon.conf` — that one is outside
this container's control.

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

## Spotify Connect (optional)

Off by default. With `ENABLE_SPOTIFY=1` the Pi shows up in the Spotify app's device
picker, and playback is handed off to it the same way as to any Connect speaker.

This runs [spotifyd](https://github.com/Spotifyd/spotifyd), built from source in the
image. Spotify removed username/password logins, so there are **no credentials to
configure** — you claim the speaker from the app over the local network. As with any
Connect device, a **Premium account is required**.

Metadata comes over MPRIS, so title, artist, album, duration and position all appear in
the web UI. Cover art does not: spotifyd's MPRIS interface doesn't publish an art URL.

> **Not verified against a real account.** The daemon builds, starts and advertises
> itself over zeroconf, but with no Premium account to claim the speaker with, the audio
> path is unproven. Note that spotifyd registers its MPRIS name only once a session is
> active — while idle it is absent from the bus, so it will not appear in the web UI
> until a phone hands playback to it, the same way Sendspin behaves.

## DLNA / UPnP (optional)

Off by default. With `ENABLE_DLNA=1` the Pi becomes a UPnP media renderer via
[gmediarender](https://github.com/hzeller/gmrender-resurrect), which is the most useful
of the optional sources if you have Android devices: AirPlay covers iOS, Spotify Connect
covers only Spotify, and DLNA covers a long tail of Android apps, BubbleUPnP,
foobar2000, Plex, Jellyfin and most NAS media servers.

It advertises as audio-only by default, so controllers don't offer it as a video
display. Its UUID is generated once and kept in `config/dlna-uuid`, so restarts don't
look like a brand-new device to controllers.

**DLNA is the one optional source that gives you cover art** — controllers send it in
the track metadata, so the UI shows real artwork rather than the placeholder.

Unlike Bluetooth and Spotify, this one *was* tested end to end: a simulated controller
pushes a track and the UI renders it.

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
  at all, and it is not verified on real hardware.
- **Spotify Connect needs a Premium account**, and is likewise unverified against one.
  Of the optional sources, only DLNA was testable end to end.
- **Cover art only comes from AirPlay and DLNA.** Sendspin, Bluetooth and Spotify all
  publish text metadata but no artwork.

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

**`couldn't create avahi client: Daemon not running!` and Shairport Sync restarting in a
loop.** The host's D-Bus socket is mounted but Avahi is not answering on it. Check on the
host with `systemctl is-active avahi-daemon` and
`dbus-send --system --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.NameHasOwner string:org.freedesktop.Avahi`.
Since 0.5.1 the container detects this and runs its own daemon instead, so it should no
longer be fatal — set `AVAHI_MODE=container` to force that behaviour.

**Endless `Host name conflict, retrying with <host>-5`.** Two Avahi daemons are
fighting: the host's and the container's. Mount `/var/run/dbus` and
`/var/run/avahi-daemon` as the compose files do, and the container will use the host's
instead. See [mDNS](#mdns-and-the-hosts-avahi).

**No AirPlay speaker appears.** Confirm host networking, then check that nothing else on
the Pi already owns ports 319/320 (`sudo ss -lunp | grep -E '319|320'`) — a host `ptpd`
or another Shairport Sync install will block nqptp. Also stop the host's `avahi-daemon`
if it conflicts, or set `ENABLE_AIRPLAY=0` to isolate the problem.

**`Failed to parse server message` / `'seek_relative' is not a valid MediaCommand`.**
Music Assistant is speaking a newer Sendspin protocol than the pinned `sendspin` release
understands: 7.5.0 requires `aiosendspin~=6.0.1`, while the library itself is much
further ahead. Audio still plays and the handshake succeeds — the message that gets
dropped is controller state, so volume changes made in Music Assistant may not reach the
player. This needs an upstream `sendspin` release; the daily watcher will open a PR when
one appears.

**Sound card busy / no audio.** `AUDIO_SHARING=dmix` is the fix for two players wanting
one card; if you set `exclusive`, expect exactly one to work at a time. Check `ALSA_PCM`
matches `aplay -l`.

**Bluetooth won't start.** `bluetoothd` logging `Failed to access management
interface` means it cannot reach the adapter: check `NET_ADMIN` is granted, that the
host's own `bluetooth` service is stopped, and that `hciconfig -a` on the host shows
the adapter. Nothing pairs? Watch `docker logs` for the `bt-agent` lines — it logs
every authorisation it accepts.

**Spotify doesn't appear in the app.** Connect discovery needs the phone on the same
subnet as the Pi, host networking (which you have), and a Premium account. Check
`docker logs` for spotifyd errors, and note that the device only appears while
spotifyd is running — `supervisorctl status` will say.

**DLNA renderer not found by a controller.** Check the port isn't firewalled and that
`docker exec sendspin-shareplay curl -s localhost:49494/description.xml` returns XML.
Some controllers cache devices aggressively; restarting the controller app helps.

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

docker exec -d np env DBUS_SESSION_BUS_ADDRESS=unix:path=/run/sendspin-shareplay/session_bus_socket \
  /opt/venv/bin/python /scripts/fake_mpris.py --name ShairportSync --title "Purple Rain"
```

[`scripts/fake_bluez.py`](scripts/fake_bluez.py) does the same for the Bluetooth side,
standing in for BlueZ on the system bus so the AVRCP reader can be exercised without a
radio:

```bash
docker exec -d np /opt/venv/bin/python /scripts/fake_bluez.py --alias "Test Phone"
```

And [`scripts/fake_dlna_controller.py`](scripts/fake_dlna_controller.py) acts as a DLNA
controller: it generates a tone, serves it, and pushes it to the renderer with metadata,
exactly as BubbleUPnP or a NAS would. `--pause` stops right after starting, which keeps
the track observable on a machine with no sound card:

```bash
docker exec -d np /opt/venv/bin/python /scripts/fake_dlna_controller.py --pause --hold 300
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
| [`build.yml`](.github/workflows/build.yml) | push to `main`, tags, PRs, weekly cron, `repository_dispatch` | Builds `linux/amd64` and `linux/arm64` in parallel on native runners, merges them into one multi-platform tag on GHCR with build provenance, then smoke-tests it |
| [`upstream-watch.yml`](.github/workflows/upstream-watch.yml) | daily cron | Checks shairport-sync, nqptp and sendspin releases; opens a PR bumping `versions.env` when one moves |
| [`lint.yml`](.github/workflows/lint.yml) | push, PR | hadolint, shellcheck, ruff |

**Why the build is split by architecture.** This image compiles shairport-sync, nqptp
and spotifyd from source. Building arm64 under QEMU on an x86 runner made a cold build
take about 18 minutes, most of it emulation overhead. Each architecture now builds on a
runner of its own kind — GitHub's `ubuntu-24.04-arm` runners are free for public repos —
pushes an untagged image by digest, and a small merge job assembles those digests into
the tags people actually pull. The two builds run concurrently, so wall-clock time is
whichever architecture is slower rather than the sum.

Layer caching is per-architecture (`type=gha` with a scope per platform, or they evict
each other), so a change that only touches `web/` or `rootfs/` reuses the compile stages
entirely — those builds land in a couple of minutes.

The Dockerfile also uses BuildKit cache mounts for apt, cargo and pip. Those do **not**
persist between CI runs, since each runner starts clean; they are there to make local
rebuilds fast, and they keep the downloaded archives out of the image layers.

Merging an upstream-bump PR rebuilds and republishes automatically. The weekly cron
rebuild picks up Debian security updates even when nothing in the repo changed.

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
- [spotifyd](https://github.com/Spotifyd/spotifyd) and [librespot](https://github.com/librespot-org/librespot)
- [gmediarender / gmrender-resurrect](https://github.com/hzeller/gmrender-resurrect) by Henner Zeller
- [BlueZ](http://www.bluez.org/) and [bluez-alsa](https://github.com/arkq/bluez-alsa)

Licensed under the [MIT License](LICENSE). Each bundled project carries its own license.
