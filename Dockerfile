# syntax=docker/dockerfile:1.7
#
# hackstack-sendspin-shairport-sync
#
# Sendspin + Shairport Sync (AirPlay 2) in one image, with a unified
# "now playing" web UI.
#
# Both players expose MPRIS on a private D-Bus session bus inside the container;
# the web UI is an MPRIS observer, so it shows whichever source is playing.

ARG DEBIAN_SUITE=trixie
ARG SHAIRPORT_SYNC_VERSION=5.2.3
ARG NQPTP_VERSION=1.2.8
ARG SENDSPIN_VERSION=7.5.0
ARG SPOTIFYD_VERSION=0.4.2

##############################################################################
# Stage 1: compile nqptp and shairport-sync
##############################################################################
FROM debian:${DEBIAN_SUITE}-slim AS native-builder

ARG SHAIRPORT_SYNC_VERSION
ARG NQPTP_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update && apt-get install -y --no-install-recommends \
        autoconf \
        automake \
        build-essential \
        ca-certificates \
        git \
        libasound2-dev \
        libavahi-client-dev \
        libavcodec-dev \
        libavformat-dev \
        libavutil-dev \
        libswresample-dev \
        libconfig-dev \
        libgcrypt-dev \
        libglib2.0-dev \
        libplist-dev \
        libplist-utils \
        libpopt-dev \
        libsodium-dev \
        libsoxr-dev \
        libssl-dev \
        libtool \
        pkg-config \
        uuid-dev \
        xxd \
    ;

# --- nqptp: the PTP timing helper AirPlay 2 depends on -----------------------
WORKDIR /src
RUN git clone --depth 1 --branch "${NQPTP_VERSION}" https://github.com/mikebrady/nqptp.git nqptp

WORKDIR /src/nqptp
RUN autoreconf -fi \
    && ./configure --prefix=/usr/local \
    && make -j"$(nproc)" \
    && make install DESTDIR=/out

# --- shairport-sync ----------------------------------------------------------
WORKDIR /src
RUN git clone --depth 1 --branch "${SHAIRPORT_SYNC_VERSION}" \
        https://github.com/mikebrady/shairport-sync.git shairport-sync

WORKDIR /src/shairport-sync
RUN autoreconf -fi \
    && ./configure \
        --prefix=/usr/local \
        --sysconfdir=/etc \
        --with-alsa \
        --with-pipe \
        --with-stdout \
        --with-dummy \
        --with-avahi \
        --with-ssl=openssl \
        --with-soxr \
        --with-airplay-2 \
        --with-ffmpeg \
        --with-metadata \
        --with-dbus-interface \
        --with-mpris-interface \
    && make -j"$(nproc)" \
    && make install DESTDIR=/out

##############################################################################
# Stage 2: build spotifyd (Spotify Connect)
#
# The published binaries link against OpenSSL 1.1, which Debian trixie does not
# ship, so build from source. Only the two features we need, which keeps this to
# roughly two minutes even under emulation.
##############################################################################
FROM rust:1-trixie AS spotifyd-builder

ARG SPOTIFYD_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update && apt-get install -y --no-install-recommends \
        libasound2-dev \
        libdbus-1-dev \
        pkg-config \
    ;

RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,target=/build/target,sharing=locked \
    CARGO_TARGET_DIR=/build/target cargo install spotifyd \
        --version "${SPOTIFYD_VERSION}" \
        --locked \
        --no-default-features \
        --features alsa_backend,dbus_mpris \
        --root /out

##############################################################################
# Stage 3: build the Python virtualenv (sendspin CLI + the web UI)
##############################################################################
FROM debian:${DEBIAN_SUITE}-slim AS python-builder

ARG SENDSPIN_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        python3 \
        python3-dev \
        python3-venv \
    ;

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# pip and wheel are build tooling; sendspin itself is pinned below.
# hadolint ignore=DL3013
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install --upgrade pip wheel \
    && pip install "sendspin==${SENDSPIN_VERSION}"

COPY web /src/web
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    pip install /src/web

##############################################################################
# Stage 4: runtime
##############################################################################
FROM debian:${DEBIAN_SUITE}-slim

ARG SHAIRPORT_SYNC_VERSION
ARG NQPTP_VERSION
ARG SENDSPIN_VERSION
ARG SPOTIFYD_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get update && apt-get install -y --no-install-recommends \
        alsa-utils \
        avahi-daemon \
        bluez \
        bluez-alsa-utils \
        ca-certificates \
        dbus \
        gmediarender \
        gstreamer1.0-alsa \
        gstreamer1.0-libav \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        libasound2t64 \
        libavahi-client3 \
        libavcodec61 \
        libavformat61 \
        libavutil59 \
        libswresample5 \
        libconfig11 \
        libdbus-1-3 \
        libgcrypt20 \
        libglib2.0-0t64 \
        libplist-2.0-4 \
        libpopt0 \
        libportaudio2 \
        libsodium23 \
        libsoxr0 \
        libssl3t64 \
        libuuid1 \
        procps \
        python3 \
        rfkill \
        supervisor \
        tzdata \
    && rm -rf /etc/avahi/services/*.service

COPY --from=native-builder /out/usr/local/bin/nqptp /usr/local/bin/nqptp
COPY --from=native-builder /out/usr/local/bin/shairport-sync /usr/local/bin/shairport-sync
COPY --from=native-builder /out/etc/shairport-sync.conf.sample /etc/shairport-sync.conf.sample
COPY --from=spotifyd-builder /out/bin/spotifyd /usr/local/bin/spotifyd
COPY --from=python-builder /opt/venv /opt/venv
COPY rootfs /

ENV PATH="/opt/venv/bin:$PATH" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/sendspin-shareplay/session_bus_socket" \
    XDG_CONFIG_HOME=/config \
    SHAIRPORT_SYNC_VERSION=${SHAIRPORT_SYNC_VERSION} \
    NQPTP_VERSION=${NQPTP_VERSION} \
    SENDSPIN_VERSION=${SENDSPIN_VERSION} \
    SPOTIFYD_VERSION=${SPOTIFYD_VERSION}

# --- tunables (see README) ---------------------------------------------------
ENV ENABLE_AIRPLAY=1 \
    ENABLE_SENDSPIN=1 \
    ENABLE_WEB=1 \
    ENABLE_BLUETOOTH=0 \
    ENABLE_SPOTIFY=0 \
    ENABLE_DLNA=0 \
    ENABLE_MQTT=0 \
    MQTT_PORT=1883 \
    MQTT_DISCOVERY_PREFIX=homeassistant \
    DLNA_PORT=49494 \
    BLUETOOTH_ADAPTER=hci0 \
    BLUETOOTH_AUDIO_DEVICE=default \
    BLUETOOTH_DISCOVERABLE=1 \
    AIRPLAY_MODE=airplay2 \
    AUDIO_SHARING=dmix \
    ALSA_PCM=hw:0,0 \
    ALSA_RATE=44100 \
    SHAIRPORT_OUTPUT_DEVICE=default \
    SENDSPIN_AUDIO_DEVICE=default \
    WEB_PORT=80

RUN mkdir -p /run/dbus /run/bluetooth /run/sendspin-shareplay /config /var/lib/shairport-sync/coverart \
    && chmod +x /usr/local/bin/*.sh

VOLUME ["/config"]
EXPOSE 80/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/usr/local/bin/healthcheck.sh"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]

LABEL org.opencontainers.image.title="hackstack-sendspin-shairport-sync" \
      org.opencontainers.image.description="Sendspin + Shairport Sync (AirPlay 2) with a unified now-playing web UI" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/romkey/hackstack-sendspin-shairport-sync"
