/* Unified now-playing UI. Reads combined MPRIS state over server-sent events. */
(() => {
  "use strict";

  const el = (id) => document.getElementById(id);
  const dom = {
    app: el("app"),
    backdrop: el("backdrop"),
    art: el("art"),
    artPlaceholder: el("art-placeholder"),
    source: el("source"),
    title: el("title"),
    artist: el("artist"),
    album: el("album"),
    progress: el("progress"),
    barFill: el("bar-fill"),
    elapsed: el("elapsed"),
    duration: el("duration"),
    players: el("players"),
    conn: el("conn"),
  };

  const SOURCE_LABEL = {
    airplay: "AirPlay",
    sendspin: "Sendspin",
    bluetooth: "Bluetooth",
    spotify: "Spotify Connect",
    dlna: "DLNA",
    other: "MPRIS",
  };

  // Position is sampled about once a second; interpolate between samples so the
  // progress bar moves smoothly.
  let clock = null; // { positionUs, lengthUs, sampledAt, playing }
  let lastArtSrc = "";

  const fmt = (us) => {
    if (!Number.isFinite(us) || us < 0) return "0:00";
    const total = Math.floor(us / 1e6);
    const m = Math.floor(total / 60);
    const s = String(total % 60).padStart(2, "0");
    return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${s}` : `${m}:${s}`;
  };

  const artHref = (url) => {
    if (!url) return "";
    if (/^https?:\/\//i.test(url)) return url;
    return `/api/art?u=${encodeURIComponent(url)}`;
  };

  function setArt(url) {
    const src = artHref(url);
    if (src === lastArtSrc) return;
    lastArtSrc = src;

    if (!src) {
      dom.art.hidden = true;
      dom.art.removeAttribute("src");
      dom.artPlaceholder.hidden = false;
      dom.backdrop.classList.remove("on");
      dom.backdrop.style.backgroundImage = "";
      return;
    }
    dom.art.onload = () => {
      dom.art.hidden = false;
      dom.artPlaceholder.hidden = true;
      dom.backdrop.style.backgroundImage = `url("${src}")`;
      dom.backdrop.classList.add("on");
    };
    dom.art.onerror = () => {
      lastArtSrc = "";
      dom.art.hidden = true;
      dom.artPlaceholder.hidden = false;
    };
    dom.art.src = src;
  }

  function renderChips(players, activeName) {
    dom.players.replaceChildren(
      ...players.map((p) => {
        const chip = document.createElement("span");
        chip.className = "chip" + (p.id === activeName ? " active" : "");
        chip.dataset.source = p.source;
        chip.dataset.status = p.status;

        const dot = document.createElement("span");
        dot.className = "dot";
        chip.append(dot, `${p.identity} · ${p.status.toLowerCase()}`);
        return chip;
      }),
    );
    if (!players.length) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = "no players detected";
      dom.players.replaceChildren(chip);
    }
  }

  function render(state) {
    const players = state.players || [];
    const active = players.find((p) => p.id === state.active) || null;
    renderChips(players, state.active);

    const hasTrack = active && (active.title || active.artist || active.album);
    dom.app.classList.toggle("idle", !hasTrack);

    if (!active || !hasTrack) {
      dom.source.hidden = true;
      dom.title.textContent = players.length ? "Nothing playing" : "Waiting for a player";
      dom.artist.textContent = "";
      dom.album.textContent = "";
      dom.progress.hidden = true;
      setArt(null);
      clock = null;
      document.title = "Now Playing";
      return;
    }

    dom.source.hidden = false;
    dom.source.dataset.source = active.source;
    dom.source.textContent =
      (SOURCE_LABEL[active.source] || active.identity) +
      (active.status === "Paused" ? " · paused" : "");

    dom.title.textContent = active.title || "Unknown track";
    dom.artist.textContent = active.artist || "";
    dom.album.textContent = active.album || "";
    setArt(active.art_url);

    document.title = active.artist
      ? `${active.title || "Unknown track"} — ${active.artist}`
      : active.title || "Now Playing";

    if (active.length_us) {
      dom.progress.hidden = false;
      dom.duration.textContent = fmt(active.length_us);
      clock = {
        positionUs: active.position_us || 0,
        lengthUs: active.length_us,
        sampledAt: performance.now(),
        playing: active.status === "Playing",
      };
      tick();
    } else {
      dom.progress.hidden = true;
      clock = null;
    }
  }

  function tick() {
    if (!clock) return;
    const drift = clock.playing ? (performance.now() - clock.sampledAt) * 1000 : 0;
    const pos = Math.min(clock.positionUs + drift, clock.lengthUs);
    dom.elapsed.textContent = fmt(pos);
    dom.barFill.style.width = `${Math.max(0, Math.min(100, (pos / clock.lengthUs) * 100))}%`;
  }
  setInterval(tick, 250);

  function connect() {
    dom.conn.dataset.state = "connecting";
    dom.conn.textContent = "connecting…";

    const source = new EventSource("/api/events");

    source.onopen = () => {
      dom.conn.dataset.state = "live";
      dom.conn.textContent = "live";
    };
    source.onmessage = (event) => {
      try {
        render(JSON.parse(event.data));
      } catch (err) {
        console.error("bad state payload", err);
      }
    };
    source.onerror = () => {
      dom.conn.dataset.state = "lost";
      dom.conn.textContent = "reconnecting…";
      source.close();
      setTimeout(connect, 3000);
    };
  }

  connect();
})();
