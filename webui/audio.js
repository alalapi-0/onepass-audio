import { state, setFocusedPlayer } from "./state.js";

const REGION_COLORS = {
  keep: "rgba(34, 197, 94, 0.35)",
  delete: "rgba(248, 113, 113, 0.35)",
  undecided: "rgba(234, 179, 8, 0.35)",
};

function resolveColor(label, selected) {
  const base = REGION_COLORS[label] || REGION_COLORS.undecided;
  if (!selected) {
    return base;
  }
  if (label === "keep") {
    return "rgba(34, 197, 94, 0.55)";
  }
  if (label === "delete") {
    return "rgba(248, 113, 113, 0.55)";
  }
  return "rgba(234, 179, 8, 0.55)";
}

function createWavesurferInstance(container) {
  if (!window.WaveSurfer || !window.WaveSurfer.create) {
    throw new Error("Wavesurfer 尚未加载");
  }
  const ws = window.WaveSurfer.create({
    container,
    height: 140,
    waveColor: "rgba(255,255,255,0.3)",
    progressColor: "rgba(59, 160, 255, 0.6)",
    cursorColor: "#3ba0ff",
    responsive: true,
    normalize: true,
    plugins: [window.WaveSurfer.regions.create()],
  });
  return ws;
}

export class Player {
  constructor({ container, type, onRegionEvent, onReady, onPlayStateChange, onSeek }) {
    this.type = type;
    this.container = container;
    this.wave = createWavesurferInstance(container);
    this.onRegionEvent = onRegionEvent;
    this.onReady = onReady;
    this.onPlayStateChange = onPlayStateChange;
    this.onSeek = onSeek;
    this._setupEvents();
    this.dragEnabled = false;
  }

  _setupEvents() {
    this.wave.on("ready", () => {
      this.onReady?.(this);
    });
    this.wave.on("play", () => {
      this.onPlayStateChange?.(this, true);
    });
    this.wave.on("pause", () => {
      this.onPlayStateChange?.(this, false);
    });
    this.wave.on("interaction", () => {
      setFocusedPlayer(this.type);
    });
    this.wave.on("seeking", (progress) => {
      this.onSeek?.(this, progress * this.wave.getDuration());
    });
    this.wave.on("region-click", (region, event) => {
      event?.stopPropagation?.();
      this.onRegionEvent?.({ type: "click", region, player: this });
    });
    this.wave.on("region-updated", (region) => {
      if (region.dragging || region.resizing) {
        return;
      }
      this.onRegionEvent?.({ type: "update", region, player: this });
    });
    this.wave.on("region-update-end", (region) => {
      this.onRegionEvent?.({ type: "update-end", region, player: this });
    });
    this.wave.on("region-created", (region) => {
      if (region.__internalCreating) {
        delete region.__internalCreating;
        return;
      }
      this.onRegionEvent?.({ type: "create", region, player: this });
    });
  }

  async load(url) {
    if (!url) {
      this.wave.empty();
      return;
    }
    this.wave.load(url);
  }

  clearRegions() {
    this.wave.regions.clear();
  }

  createRegion(segment, selected) {
    const id = segment.id;
    const existing = this.wave.regions.list[id];
    if (existing) {
      existing.remove();
    }
    const region = this.wave.addRegion({
      id,
      start: segment.start,
      end: segment.end,
      color: resolveColor(segment.label, selected),
      drag: true,
      resize: true,
    });
    region.__internalCreating = true;
    return region;
  }

  updateRegion(segment, selected) {
    const region = this.wave.regions.list[segment.id];
    if (!region) {
      return this.createRegion(segment, selected);
    }
    region.update({
      start: segment.start,
      end: segment.end,
      color: resolveColor(segment.label, selected),
    });
    return region;
  }

  removeRegion(id) {
    const region = this.wave.regions.list[id];
    region?.remove();
  }

  playPause() {
    this.wave.playPause();
  }

  play() {
    this.wave.play();
  }

  pause() {
    this.wave.pause();
  }

  seekToTime(time) {
    const duration = this.wave.getDuration();
    if (duration <= 0) {
      return;
    }
    const clamped = Math.max(0, Math.min(time, duration));
    this.wave.seekTo(clamped / duration);
  }

  getCurrentTime() {
    return this.wave.getCurrentTime();
  }

  getDuration() {
    return this.wave.getDuration();
  }

  enableAltDragSelection() {
    if (this.dragEnabled) {
      return;
    }
    this.dragEnabled = true;
    this.wave.enableDragSelection({
      color: resolveColor("undecided", false),
    });
  }

  disableDragSelection() {
    if (!this.dragEnabled) {
      return;
    }
    this.dragEnabled = false;
    this.wave.disableDragSelection();
  }
}

export class DualWaveController {
  constructor({ onRegionEvent, onPlayStateChange, onSeek }) {
    this.players = {
      source: new Player({
        container: "#wave-source",
        type: "source",
        onRegionEvent,
        onReady: () => {},
        onPlayStateChange,
        onSeek,
      }),
      rendered: new Player({
        container: "#wave-rendered",
        type: "rendered",
        onRegionEvent,
        onReady: () => {},
        onPlayStateChange,
        onSeek,
      }),
    };
    this.syncing = false;
  }

  getPlayer(type) {
    return this.players[type];
  }

  loadSources({ source, rendered }) {
    this.players.source.load(source);
    this.players.rendered.load(rendered);
  }

  setRegion(segment, selectedId) {
    const selected = segment.id === selectedId;
    this.players.source.updateRegion(segment, selected);
    this.players.rendered.updateRegion(segment, selected);
  }

  removeRegion(id) {
    this.players.source.removeRegion(id);
    this.players.rendered.removeRegion(id);
  }

  clearRegions() {
    this.players.source.clearRegions();
    this.players.rendered.clearRegions();
  }

  playPause(type) {
    const player = this.players[type];
    if (!player) return;
    player.playPause();
  }

  playBoth(from) {
    if (state.syncPlayback) {
      this._syncAction(from, (target) => target.play());
    }
  }

  pauseBoth(from) {
    if (state.syncPlayback) {
      this._syncAction(from, (target) => target.pause());
    }
  }

  seekBoth(from, time) {
    if (state.syncPlayback) {
      this._syncAction(from, (target) => target.seekToTime(time));
    }
  }

  _syncAction(from, callback) {
    if (!state.syncPlayback) return;
    if (this.syncing) return;
    this.syncing = true;
    try {
      const otherKey = from.type === "source" ? "rendered" : "source";
      const other = this.players[otherKey];
      callback(other);
    } finally {
      this.syncing = false;
    }
  }
}

