export const LABEL_ORDER = ["keep", "delete", "undecided"];

export const state = {
  stems: [],
  currentStem: null,
  edlModel: null,
  csvModel: null,
  renderedAudioUrl: null,
  sourceAudioUrl: null,
  selectedSegmentId: null,
  syncPlayback: true,
  focusedPlayer: "source",
  dirty: {
    edl: false,
    csv: false,
  },
};

export function resetDirty() {
  state.dirty.edl = false;
  state.dirty.csv = false;
}

export function markDirty(type) {
  if (type in state.dirty) {
    state.dirty[type] = true;
  }
}

export function setSyncPlayback(enabled) {
  state.syncPlayback = Boolean(enabled);
}

export function setFocusedPlayer(name) {
  state.focusedPlayer = name;
}

export function setSelectedSegment(id) {
  state.selectedSegmentId = id;
}

export function getSelectedSegment() {
  if (!state.edlModel || !Array.isArray(state.edlModel.segments)) {
    return null;
  }
  return state.edlModel.segments.find((segment) => segment.id === state.selectedSegmentId) || null;
}

export function clearModels() {
  state.edlModel = null;
  state.csvModel = null;
  state.selectedSegmentId = null;
  state.sourceAudioUrl = null;
  state.renderedAudioUrl = null;
  resetDirty();
}

