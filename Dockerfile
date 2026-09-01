# syntax=docker/dockerfile:1.7
#
# sendspin-shareplay -- Sendspin + Shairport Sync (AirPlay 2) in one image,
# with a unified "now playing" web UI.
#
# Both players expose MPRIS on a private D-Bus session bus inside the container;
# the web UI is an MPRIS observer, so it shows whichever source is playing.

ARG DEBIAN_SUITE=trixie
ARG SHAIRPORT_SYNC_VERSION=5.2.3
ARG NQPTP_VERSION=1.2.8
ARG SENDSPIN_VERSION=7.5.0

##############################################################################
# Stage 1: compile nqptp and shairport-sync
##############################################################################
FROM debian:${DEBIAN_SUITE}-slim AS native-builder

ARG SHAIRPORT_SYNC_VERSION
ARG NQPTP_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
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
    && rm -rf /var/lib/apt/lists/*

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
# Stage 2: build the Python virtualenv (sendspin CLI + the web UI)
##############################################################################
FROM debian:${DEBIAN_SUITE}-slim AS python-builder

ARG SENDSPIN_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        python3 \
        python3-dev \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# pip and wheel are build tooling; sendspin itself is pinned below.
# hadolint ignore=DL3013
RUN pip install --no-cache-dir --upgrade pip wheel \
    && pip install --no-cache-dir "sendspin==${SENDSPIN_VERSION}"

COPY web /src/web
RUN pip install --no-cache-dir /src/web

##############################################################################
# Stage 3: runtime
##############################################################################
FROM debian:${DEBIAN_SUITE}-slim

ARG SHAIRPORT_SYNC_VERSION
ARG NQPTP_VERSION
ARG SENDSPIN_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        alsa-utils \
        avahi-daemon \
        ca-certificates \
        dbus \
        libasound2t64 \
        libavahi-client3 \
        libavcodec61 \
        libavformat61 \
        libavutil59 \
        libswresample5 \
        libconfig11 \
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
        supervisor \
        tzdata \
    && rm -rf /var/lib/apt/lists/* /etc/avahi/services/*.service

COPY --from=native-builder /out/usr/local/bin/nqptp /usr/local/bin/nqptp
COPY --from=native-builder /out/usr/local/bin/shairport-sync /usr/local/bin/shairport-sync
COPY --from=native-builder /out/etc/shairport-sync.conf.sample /etc/shairport-sync.conf.sample
COPY --from=python-builder /opt/venv /opt/venv
COPY rootfs /

ENV PATH="/opt/venv/bin:$PATH" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/dbus/session_bus_socket" \
    XDG_CONFIG_HOME=/config \
    SHAIRPORT_SYNC_VERSION=${SHAIRPORT_SYNC_VERSION} \
    NQPTP_VERSION=${NQPTP_VERSION} \
    SENDSPIN_VERSION=${SENDSPIN_VERSION}

# --- tunables (see README) ---------------------------------------------------
ENV ENABLE_AIRPLAY=1 \
    ENABLE_SENDSPIN=1 \
    ENABLE_WEB=1 \
    AIRPLAY_MODE=airplay2 \
    AUDIO_SHARING=dmix \
    ALSA_PCM=hw:0,0 \
    ALSA_RATE=44100 \
    SHAIRPORT_OUTPUT_DEVICE=default \
    SENDSPIN_AUDIO_DEVICE=default \
    WEB_PORT=8080

RUN mkdir -p /run/dbus /config /var/lib/shairport-sync/coverart \
    && chmod +x /usr/local/bin/*.sh

VOLUME ["/config"]
EXPOSE 8080/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["/usr/local/bin/healthcheck.sh"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]

LABEL org.opencontainers.image.title="sendspin-shareplay" \
      org.opencontainers.image.description="Sendspin + Shairport Sync (AirPlay 2) with a unified now-playing web UI" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/romkey/sendspin-shareplay"
