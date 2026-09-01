# `config/`

This directory is mounted at `/config` inside the container. It holds three things:

| Path | Who writes it | What it does |
| --- | --- | --- |
| `shairport-sync.conf` | you | Used verbatim instead of the config the entrypoint generates from `.env` |
| `asound.conf` | you | Installed as `/etc/asound.conf` instead of the generated ALSA setup |
| `bluetooth.conf` | you | Installed as `/etc/bluetooth/main.conf` instead of the generated BlueZ config |
| `sendspin/` | the Sendspin daemon | Persistent client settings — volume, delay, remembered servers |
| `bluetooth/` | BlueZ | Pairing keys, so paired phones survive a rebuild |
| `spotifyd/` | spotifyd | Spotify Connect credentials and audio cache |
| `dlna-uuid` | the entrypoint | The renderer's stable UPnP UUID, generated once |

Nothing here is required. With an empty `config/`, the entrypoint generates both
config files from the environment variables in `.env`, which is enough for most
setups.

## Using an example

Drop the `.example` suffix:

```bash
cp config/shairport-sync.conf.example config/shairport-sync.conf
```

Then edit it and restart:

```bash
docker compose -f docker-compose.prod.yml restart
```

The entrypoint copies the file on every start, so a restart is enough to pick up
changes — no rebuild.

## Two things to watch

**Keep the MPRIS bus setting.** If you write your own `shairport-sync.conf`,
keep `mpris_service_bus = "session";` in the `general` block. The web UI reads
both players over the container's private D-Bus session bus, and Shairport Sync
defaults to the system bus, so dropping this line makes AirPlay tracks disappear
from the UI.

**Your file wins completely.** These are not merged with the generated config.
Once `config/shairport-sync.conf` exists, the matching `.env` variables
(`AIRPLAY_NAME`, `SHAIRPORT_OUTPUT_DEVICE`, `ALSA_MIXER_CONTROL`) stop having
any effect — set them in the file instead. The same applies to `asound.conf` and
`AUDIO_SHARING` / `ALSA_PCM` / `ALSA_RATE`.
