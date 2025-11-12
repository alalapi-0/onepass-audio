import { listStems, fetchStem, saveEdl, saveCsv, uploadFile } from "./api.js";
import {
  state,
  clearModels,
  markDirty,
  resetDirty,
  setSyncPlayback,
  setFocusedPlayer,
  setSelectedSegment,
  getSelectedSegment,
  LABEL_ORDER,
} from "./state.js";
import { DualWaveController } from "./audio.js";
import {
  initUI,
  renderStemsList,
  updateStemHeader,
  updatePlayerMeta,
  updatePlayerStatus,
  renderSegmentsTable,
  showToast,
  showError,
  toggleDropMask,
  openFileDialog,
  highlightSegment,
  bindGlobalShortcuts,
  updateSyncToggle,
  promptAudioType,
} from "./ui.js";

const CSV_HEADER = ["Name", "Start", "End", "Duration", "Comment"];
let waveController;
let statusTimer = null;

function generateSegmentId() {
  return `seg_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function defaultEdl(stem) {
  return {
    version: "R6",
    stem,
    source_audio: null,
    rendered_audio: null,
    segments: [],
    meta: {},
  };
}

function normalizeLabel(label) {
  if (typeof label === "string") {
    const normalized = label.toLowerCase();
    if (LABEL_ORDER.includes(normalized)) {
      return normalized;
    }
  }
  if (label === true) {
    return "keep";
  }
  if (label === false) {
    return "delete";
  }
  return "undecided";
}

function parseEdlText(text, stem) {
  if (!text || !text.trim()) {
    return defaultEdl(stem);
  }
  try {
    const parsed = JSON.parse(text);
    const edl = {
      ...defaultEdl(stem),
      ...parsed,
    };
    if (!Array.isArray(edl.segments) || !edl.segments.length) {
      const actions = Array.isArray(parsed?.actions) ? parsed.actions : [];
      edl.segments = actions
        .map((action) => ({
          start: Number(action.start ?? action.begin ?? 0),
          end: Number(action.end ?? action.finish ?? 0),
          label: action.keep === false ? "delete" : "keep",
          text: action.text || action.label || "",
        }))
        .filter((item) => Number.isFinite(item.start) && Number.isFinite(item.end));
    }
    edl.segments = (edl.segments || []).map((segment) => {
      const start = Number(segment.start ?? 0);
      const end = Number(segment.end ?? 0);
      return {
        id: segment.id || generateSegmentId(),
        start: Number.isFinite(start) ? start : 0,
        end: Number.isFinite(end) ? end : 0,
        label: normalizeLabel(segment.label),
        text: segment.text || segment.name || "",
        name: segment.name || "",
        comment: segment.comment || segment.text || "",
      };
    });
    return edl;
  } catch (error) {
    throw new Error(`EDL 解析失败: ${error.message}`);
  }
}

function parseCsvLine(line) {
  const result = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i];
    if (char === "\"") {
      if (inQuotes && line[i + 1] === "\"") {
        current += "\"";
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (char === "," && !inQuotes) {
      result.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  result.push(current);
  return result;
}

function parseCsvText(text) {
  if (!text || !text.trim()) {
    return [];
  }
  const normalizedText = text.replace(/^\ufeff/, "");
  const lines = normalizedText
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .filter((line) => line.trim().length > 0);
  if (!lines.length) {
    return [];
  }
  const header = parseCsvLine(lines[0]);
  if (header.length < CSV_HEADER.length) {
    throw new Error("CSV 表头缺失");
  }
  const normalizedHeader = header.map((item) => item.trim());
  for (let i = 0; i < CSV_HEADER.length; i += 1) {
    if (normalizedHeader[i] !== CSV_HEADER[i]) {
      throw new Error(`CSV 表头不正确，期望 ${CSV_HEADER.join(",")}`);
    }
  }
  const rows = [];
  for (let lineIndex = 1; lineIndex < lines.length; lineIndex += 1) {
    const values = parseCsvLine(lines[lineIndex]);
    if (!values.length || values.every((value) => value.trim() === "")) {
      continue;
    }
    const row = {};
    for (let i = 0; i < CSV_HEADER.length; i += 1) {
      row[CSV_HEADER[i]] = values[i] ?? "";
    }
    const start = Number.parseFloat(row.Start);
    const end = Number.parseFloat(row.End);
    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      throw new Error(`CSV 第 ${lineIndex + 1} 行的时间格式无效`);
    }
    if (start < 0 || end < 0) {
      throw new Error(`CSV 第 ${lineIndex + 1} 行存在负数时间`);
    }
    if (end <= start) {
      throw new Error(`CSV 第 ${lineIndex + 1} 行结束时间需大于开始时间`);
    }
    rows.push({
      id: generateSegmentId(),
      Name: row.Name?.trim() ?? "",
      Start: start,
      End: end,
      Duration: Math.max(end - start, 0),
      Comment: row.Comment ?? "",
    });
  }
  return rows;
}

function syncCsvWithSegments(edl, csvRows) {
  const segments = edl.segments || [];
  const length = Math.max(segments.length, csvRows.length);
  const rows = [];
  for (let i = 0; i < length; i += 1) {
    let segment = segments[i];
    let row = csvRows[i];
    if (!segment) {
      segment = {
        id: generateSegmentId(),
        start: row ? Number(row.Start) || 0 : 0,
        end: row ? Number(row.End) || 0 : 0,
        label: "undecided",
        text: "",
        name: "",
        comment: "",
      };
      segments.push(segment);
    }
    segment.id = segment.id || generateSegmentId();
    segment.label = normalizeLabel(segment.label);
    if (row) {
      segment.start = Number(row.Start) || 0;
      segment.end = Number(row.End) || 0;
      segment.name = row.Name || segment.name || "";
      segment.comment = row.Comment || segment.comment || "";
      segment.text = segment.comment || segment.text || "";
    }
    row = {
      id: segment.id,
      Name: segment.name || "",
      Start: Number.parseFloat(segment.start.toFixed(3)),
      End: Number.parseFloat(segment.end.toFixed(3)),
      Duration: Number.parseFloat(Math.max(segment.end - segment.start, 0).toFixed(3)),
      Comment: segment.comment || "",
    };
    rows.push(row);
  }
  edl.segments = segments;
  return rows;
}

function serializeCsv(rows) {
  const lines = [];
  lines.push(CSV_HEADER.join(","));
  rows.forEach((row) => {
    const values = CSV_HEADER.map((key) => {
      let value = row[key] ?? "";
      if (typeof value === "number") {
        value = value.toFixed(3);
      }
      const text = String(value ?? "");
      if (text.includes(",") || text.includes("\"") || text.includes("\n")) {
        return `"${text.replace(/"/g, '""')}"`;
      }
      return text;
    });
    lines.push(values.join(","));
  });
  const joined = lines.join("\n");
  return `\ufeff${joined}`;
}

function sortSegments() {
  state.edlModel.segments.sort((a, b) => a.start - b.start);
  state.csvModel.rows.sort((a, b) => {
    const segA = state.edlModel.segments.find((item) => item.id === a.id);
    const segB = state.edlModel.segments.find((item) => item.id === b.id);
    if (!segA || !segB) return 0;
    return segA.start - segB.start;
  });
}

function syncSegmentsToCsv() {
  state.csvModel.rows = state.edlModel.segments.map((segment) => ({
    id: segment.id,
    Name: segment.name || "",
    Start: Number.parseFloat(segment.start.toFixed(3)),
    End: Number.parseFloat(segment.end.toFixed(3)),
    Duration: Number.parseFloat(Math.max(segment.end - segment.start, 0).toFixed(3)),
    Comment: segment.comment || "",
  }));
}

function updateRegions() {
  if (!state.edlModel) return;
  state.edlModel.segments.forEach((segment) => {
    waveController.setRegion(segment, state.selectedSegmentId);
  });
}

function renderSegments() {
  if (!state.edlModel) {
    renderSegmentsTable([], null, { onSelect: () => {}, onFieldChange: () => {} });
    waveController.clearRegions();
    return;
  }
  sortSegments();
  syncSegmentsToCsv();
  renderSegmentsTable(state.edlModel.segments, state.selectedSegmentId, {
    onSelect: (id) => selectSegment(id, { seek: true }),
    onFieldChange: handleSegmentFieldChange,
  });
  waveController.clearRegions();
  updateRegions();
}

function handleSegmentFieldChange(id, field, value) {
  const segment = state.edlModel?.segments.find((item) => item.id === id);
  if (!segment) return;
  const row = state.csvModel.rows.find((item) => item.id === id);
  if (field === "label") {
    segment.label = normalizeLabel(value);
    markDirty("edl");
    waveController.setRegion(segment, state.selectedSegmentId);
  } else if (field === "name") {
    segment.name = value;
    if (row) row.Name = value;
    markDirty("csv");
  } else if (field === "comment") {
    segment.comment = value;
    segment.text = value;
    if (row) row.Comment = value;
    markDirty("edl");
    markDirty("csv");
  }
}

function selectSegment(id, { seek = false } = {}) {
  setSelectedSegment(id);
  highlightSegment(id);
  updateRegions();
  const segment = state.edlModel?.segments.find((item) => item.id === id);
  if (!segment) return;
  if (seek) {
    waveController.getPlayer("source").seekToTime(segment.start);
    waveController.getPlayer("rendered").seekToTime(segment.start);
  }
}

function validateSegments() {
  const errors = [];
  state.edlModel.segments.forEach((segment, index) => {
    if (segment.end <= segment.start) {
      errors.push(`第 ${index + 1} 行结束时间需大于开始时间`);
    }
    if (segment.start < 0 || segment.end < 0) {
      errors.push(`第 ${index + 1} 行时间不能为负数`);
    }
  });
  if (errors.length) {
    throw new Error(errors.join("；"));
  }
}

async function saveAll() {
  if (!state.currentStem) {
    showError("请先选择 stem");
    return;
  }
  try {
    validateSegments();
    sortSegments();
    syncSegmentsToCsv();
    const payload = {
      version: state.edlModel.version ?? "R6",
      stem: state.currentStem,
      source_audio: state.sourceAudioUrl ?? state.edlModel.source_audio ?? null,
      rendered_audio: state.renderedAudioUrl ?? state.edlModel.rendered_audio ?? null,
      segments: state.edlModel.segments.map((segment) => ({
        start: Number.parseFloat(segment.start.toFixed(3)),
        end: Number.parseFloat(segment.end.toFixed(3)),
        label: segment.label,
        text: segment.text || segment.comment || "",
        name: segment.name || "",
        comment: segment.comment || "",
        id: segment.id,
      })),
      meta: state.edlModel.meta ?? {},
    };
    const edlText = `${JSON.stringify(payload, null, 2)}\n`;
    const csvText = serializeCsv(state.csvModel.rows);
    await Promise.all([saveEdl(state.currentStem, edlText), saveCsv(state.currentStem, csvText)]);
    showToast("保存成功，已写入 EDL 与 CSV", { title: "保存成功" });
    resetDirty();
  } catch (error) {
    showError(error.message || "保存失败");
  }
}

function updatePlayerMetaInfo(stemEntry) {
  const source = state.sourceAudioUrl || stemEntry?.source_audio || state.edlModel?.source_audio;
  const rendered = state.renderedAudioUrl || stemEntry?.clean_audio || state.edlModel?.rendered_audio;
  updatePlayerMeta("source", source ? `来源：${source}` : "未加载源音频");
  updatePlayerMeta("rendered", rendered ? `来源：${rendered}` : "未加载干净音频");
}

async function loadStem(stem) {
  if (!stem) return;
  clearModels();
  state.currentStem = stem;
  const stemEntry = state.stems.find((item) => item.stem === stem) || null;
  try {
    const data = await fetchStem(stem);
    const edl = parseEdlText(data.edl_text, stem);
    const csvRows = parseCsvText(data.csv_text);
    const rows = syncCsvWithSegments(edl, csvRows);
    state.edlModel = edl;
    state.csvModel = { rows };
    state.sourceAudioUrl = edl.source_audio || stemEntry?.source_audio || null;
    state.renderedAudioUrl = edl.rendered_audio || stemEntry?.clean_audio || null;
    state.edlModel.source_audio = state.sourceAudioUrl;
    state.edlModel.rendered_audio = state.renderedAudioUrl;
    renderStemsList(state.stems, stem);
    updateStemHeader({ stem, source: state.sourceAudioUrl, rendered: state.renderedAudioUrl });
    updatePlayerMetaInfo(stemEntry);
    waveController.loadSources({ source: state.sourceAudioUrl, rendered: state.renderedAudioUrl });
    renderSegments();
    if (state.edlModel.segments.length) {
      selectSegment(state.edlModel.segments[0].id);
    }
    showToast(`已加载 ${stem}`, { title: "加载完成" });
  } catch (error) {
    showError(error.message || "加载失败");
  }
}

async function refreshStems(selectFromQuery = false) {
  try {
    state.stems = await listStems();
    renderStemsList(state.stems, state.currentStem);
    if (selectFromQuery) {
      const params = new URLSearchParams(window.location.search);
      const targetStem = params.get("stem");
      if (targetStem) {
        await loadStem(targetStem);
        return;
      }
    }
    if (state.currentStem) {
      await loadStem(state.currentStem);
    }
  } catch (error) {
    showError(error.message || "获取 stem 列表失败");
  }
}

function handlePlayButton(type) {
  setFocusedPlayer(type);
  waveController.playPause(type);
}

function setupKeyboardShortcuts() {
  bindGlobalShortcuts((event) => {
    if (!state.currentStem) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveAll();
      return;
    }
    if (event.key === " " && !event.ctrlKey && !event.metaKey) {
      const active = state.focusedPlayer || "source";
      event.preventDefault();
      waveController.playPause(active);
      return;
    }
    if (event.key === "Delete") {
      event.preventDefault();
      removeSelectedSegment();
      return;
    }
    if (event.key.toLowerCase() === "d") {
      event.preventDefault();
      toggleSelectedSegmentLabel();
      return;
    }
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      moveSelection(event.key === "ArrowRight" ? 1 : -1);
    }
  });
}

function removeSelectedSegment() {
  const selected = getSelectedSegment();
  if (!selected) return;
  const index = state.edlModel.segments.findIndex((segment) => segment.id === selected.id);
  if (index >= 0) {
    state.edlModel.segments.splice(index, 1);
  }
  const csvIndex = state.csvModel.rows.findIndex((row) => row.id === selected.id);
  if (csvIndex >= 0) {
    state.csvModel.rows.splice(csvIndex, 1);
  }
  waveController.removeRegion(selected.id);
  markDirty("edl");
  markDirty("csv");
  renderSegments();
  const next = state.edlModel.segments[index] || state.edlModel.segments[index - 1];
  if (next) {
    selectSegment(next.id);
  } else {
    setSelectedSegment(null);
    highlightSegment(null);
  }
}

function toggleSelectedSegmentLabel() {
  const segment = getSelectedSegment();
  if (!segment) return;
  const currentIndex = LABEL_ORDER.indexOf(segment.label);
  const nextIndex = (currentIndex + 1) % LABEL_ORDER.length;
  segment.label = LABEL_ORDER[nextIndex];
  markDirty("edl");
  renderSegments();
  selectSegment(segment.id);
}

function moveSelection(offset) {
  if (!state.edlModel?.segments.length) return;
  const currentId = state.selectedSegmentId;
  const index = state.edlModel.segments.findIndex((segment) => segment.id === currentId);
  const nextIndex = index + offset;
  if (nextIndex >= 0 && nextIndex < state.edlModel.segments.length) {
    selectSegment(state.edlModel.segments[nextIndex].id, { seek: true });
  }
}

function setupStatusTimer() {
  if (statusTimer) {
    clearInterval(statusTimer);
  }
  statusTimer = setInterval(() => {
    ["source", "rendered"].forEach((type) => {
      const player = waveController.getPlayer(type);
      const time = player.getCurrentTime();
      const duration = player.getDuration();
      if (duration > 0) {
        updatePlayerStatus(type, `${time.toFixed(2)}s / ${duration.toFixed(2)}s`);
      } else {
        updatePlayerStatus(type, "未加载");
      }
    });
  }, 250);
}

function handleRegionEvent(event) {
  const { type, region, player } = event;
  if (type === "click") {
    selectSegment(region.id, { seek: true });
    return;
  }
  if (type === "create") {
    if (player.type !== "source") {
      region.remove();
      return;
    }
    const start = Math.min(region.start, region.end);
    const end = Math.max(region.start, region.end);
    region.remove();
    const segment = {
      id: generateSegmentId(),
      start,
      end,
      label: "undecided",
      name: "",
      comment: "",
      text: "",
    };
    state.edlModel.segments.push(segment);
    markDirty("edl");
    markDirty("csv");
    renderSegments();
    selectSegment(segment.id, { seek: false });
    return;
  }
  if (type === "update" || type === "update-end") {
    const segment = state.edlModel.segments.find((item) => item.id === region.id);
    if (!segment) return;
    segment.start = Math.min(region.start, region.end);
    segment.end = Math.max(region.start, region.end);
    markDirty("edl");
    markDirty("csv");
    renderSegments();
    selectSegment(segment.id, { seek: false });
  }
}

function handlePlayStateChange(player, playing) {
  if (playing) {
    waveController.playBoth(player);
  } else {
    waveController.pauseBoth(player);
  }
}

function handleSeek(player, time) {
  waveController.seekBoth(player, time);
}

function setupAltDragSelection() {
  ["source", "rendered"].forEach((type) => {
    const player = waveController.getPlayer(type);
    const element = document.querySelector(type === "source" ? "#wave-source" : "#wave-rendered");
    element.addEventListener("pointerdown", (event) => {
      if (event.altKey) {
        player.enableAltDragSelection();
      } else {
        player.disableDragSelection();
      }
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach((evt) => {
      element.addEventListener(evt, () => player.disableDragSelection());
    });
  });
}

function handleCsvFiles(files) {
  if (!state.currentStem) {
    showError("请先选择 stem 再导入");
    return;
  }
  const file = files[0];
  if (!file) return;
  file.text()
    .then((text) => {
      const rows = parseCsvText(text);
      const hadSegments = state.edlModel.segments.length;
      const mergedRows = syncCsvWithSegments(state.edlModel, rows);
      state.csvModel.rows = mergedRows.map((row, index) => ({
        ...row,
        id: state.edlModel.segments[index].id,
      }));
      markDirty("csv");
      markDirty("edl");
      renderSegments();
      showToast(`已导入 CSV：${file.name}`);
      if (hadSegments === 0 && state.edlModel.segments.length > 0) {
        selectSegment(state.edlModel.segments[0].id);
      }
    })
    .catch((error) => {
      showError(error.message || "解析 CSV 失败");
    });
}

function handleAudioFiles(files) {
  if (!state.currentStem) {
    showError("请先选择 stem 再导入");
    return;
  }
  const file = files[0];
  if (!file) return;
  promptAudioType()
    .then((type) => uploadFile({ stem: state.currentStem, type, file }))
    .then((response) => {
      if (response.url) {
        if (response.url.includes("/materials/")) {
          state.sourceAudioUrl = response.url;
          state.edlModel.source_audio = response.url;
        } else {
          state.renderedAudioUrl = response.url;
          state.edlModel.rendered_audio = response.url;
        }
        waveController.loadSources({ source: state.sourceAudioUrl, rendered: state.renderedAudioUrl });
        updatePlayerMetaInfo(state.stems.find((item) => item.stem === state.currentStem));
        updateStemHeader({
          stem: state.currentStem,
          source: state.sourceAudioUrl,
          rendered: state.renderedAudioUrl,
        });
      }
      showToast("音频上传成功，已更新播放源");
    })
    .catch((error) => {
      if (error.message === "已取消") return;
      showError(error.message || "上传音频失败");
    });
}

function setupDragAndDrop() {
  window.addEventListener("dragenter", (event) => {
    event.preventDefault();
    toggleDropMask(true);
  });
  window.addEventListener("dragover", (event) => {
    event.preventDefault();
    toggleDropMask(true);
  });
  window.addEventListener("dragleave", (event) => {
    if (event.target === document.documentElement || event.target === document.body) {
      toggleDropMask(false);
    }
  });
  window.addEventListener("drop", (event) => {
    event.preventDefault();
    toggleDropMask(false);
    const files = Array.from(event.dataTransfer?.files || []);
    if (!files.length) return;
    const file = files[0];
    if (file.name.toLowerCase().endsWith(".csv")) {
      handleCsvFiles([file]);
    } else {
      handleAudioFiles([file]);
    }
  });
}

function attachUiEvents() {
  initUI({
    onRefresh: () => refreshStems(false),
    onStemSelected: (stem) => loadStem(stem),
    onPlay: handlePlayButton,
    onImportCsv: () => openFileDialog({ accept: ".csv" }, handleCsvFiles),
    onImportAudio: () => openFileDialog({ accept: ".wav,.mp3,.flac,.m4a,.aac,.ogg" }, handleAudioFiles),
    onSave: saveAll,
    onToggleSync: (value) => {
      setSyncPlayback(value);
      updateSyncToggle(value);
    },
  });
}

function init() {
  waveController = new DualWaveController({
    onRegionEvent: handleRegionEvent,
    onPlayStateChange: handlePlayStateChange,
    onSeek: handleSeek,
  });
  attachUiEvents();
  setupStatusTimer();
  setupAltDragSelection();
  setupKeyboardShortcuts();
  setupDragAndDrop();
  setSyncPlayback(true);
  updateSyncToggle(true);
  refreshStems(true);
}

document.addEventListener("DOMContentLoaded", init);

