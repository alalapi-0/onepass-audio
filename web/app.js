const API_BASE = "/api"
const WaveSurferLib = window.WaveSurfer
const RegionsPlugin = WaveSurferLib?.Regions

const REGION_STATES = ["keep", "delete", "undecided"]
const REGION_COLORS = {
  keep: "rgba(34,197,94,0.35)",
  delete: "rgba(239,68,68,0.35)",
  undecided: "rgba(234,179,8,0.4)",
}
const STATE_LABELS = {
  keep: "保留",
  delete: "删除",
  undecided: "未决",
}

const state = {
  waveSurfer: null,
  regionsPlugin: null,
  dragSelectionHandle: null,
  altPressed: false,
  allowProgrammaticRegion: false,
  skipDeletes: true,
  selectedRegionId: null,
  skipGuardRegionId: null,
  stems: [],
  selectedStem: null,
  loadingStem: false,
  playbackMode: "preview",
  currentSourceId: "",
  currentAudioToken: null,
  renderedAudioToken: null,
  currentAudioFileName: "",
  currentMarkerFileName: "",
  currentStemOverride: "",
  audioObjectUrl: null,
  srtItems: [],
  activeSubtitleIndex: -1,
  pendingRender: false,
  queryStem: null,
}

const dom = {
  toastContainer: document.getElementById("toast-container"),
  stemList: document.getElementById("stem-list"),
  stemEmpty: document.getElementById("stem-empty"),
  refreshStems: document.getElementById("refresh-stems"),
  currentStemTitle: document.getElementById("current-stem-title"),
  currentStem: document.getElementById("current-stem"),
  currentAudio: document.getElementById("current-audio"),
  currentMarker: document.getElementById("current-marker"),
  playbackLabel: document.getElementById("playback-label"),
  renderButton: document.getElementById("render-audio"),
  exportEdl: document.getElementById("export-edl"),
  exportMarkers: document.getElementById("export-markers"),
  playToggle: document.getElementById("play-toggle"),
  seekBack: document.getElementById("seek-back"),
  seekForward: document.getElementById("seek-forward"),
  skipToggle: document.getElementById("skip-toggle"),
  zoomRange: document.getElementById("zoom-range"),
  regionTableBody: document.getElementById("region-tbody"),
  regionSummary: document.getElementById("region-summary"),
  srtList: document.getElementById("srt-list"),
  dropzone: document.getElementById("dropzone"),
  pickAudio: document.getElementById("pick-audio"),
  btnPickAudio: document.getElementById("btn-pick-audio"),
  pickMarkers: document.getElementById("pick-markers"),
  btnPickMarkers: document.getElementById("btn-pick-markers"),
}

const playbackTabs = Array.from(document.querySelectorAll("[data-playback]"))

if (!WaveSurferLib || !RegionsPlugin) {
  showToast("缺少 WaveSurfer 依赖，请确认 vendor/ 目录存在。", "error")
}

init()

function init() {
  state.queryStem = new URLSearchParams(window.location.search).get("stem")
  attachEvents()
  setupDragAndDrop()
  createWaveSurfer()
  updateSelectionInfo()
  fetchStemList()
}

function attachEvents() {
  dom.refreshStems?.addEventListener("click", () => fetchStemList(true))
  dom.renderButton?.addEventListener("click", (event) => requestRender(event.shiftKey))
  dom.exportEdl?.addEventListener("click", exportEdl)
  dom.exportMarkers?.addEventListener("click", exportAuditionCsv)
  dom.playToggle?.addEventListener("click", togglePlayback)
  dom.seekBack?.addEventListener("click", () => seekBy(-5))
  dom.seekForward?.addEventListener("click", () => seekBy(5))
  dom.skipToggle?.addEventListener("click", toggleSkipDeletes)
  dom.zoomRange?.addEventListener("input", handleZoomChange)
  playbackTabs.forEach((tab) => {
    tab.addEventListener("click", () => setPlaybackMode(tab.dataset.playback))
  })
  document.querySelectorAll("[data-bulk]").forEach((button) => {
    button.addEventListener("click", () => applyStateToAll(button.dataset.bulk))
  })
  if (dom.btnPickAudio && dom.pickAudio) {
    dom.btnPickAudio.addEventListener("click", () => dom.pickAudio.click())
    dom.pickAudio.addEventListener("change", (event) => {
      const [file] = event.target.files || []
      if (file) {
        handleAudioFile(file)
      }
      event.target.value = ""
    })
  }
  if (dom.btnPickMarkers && dom.pickMarkers) {
    dom.btnPickMarkers.addEventListener("click", () => dom.pickMarkers.click())
    dom.pickMarkers.addEventListener("change", (event) => {
      const [file] = event.target.files || []
      if (file) {
        handleMarkerFile(file)
      }
      event.target.value = ""
    })
  }
  window.addEventListener("keydown", handleKeyDown)
  window.addEventListener("keyup", handleKeyUp)
}

function createWaveSurfer() {
  if (!WaveSurferLib || !RegionsPlugin) {
    return
  }
  if (state.waveSurfer) {
    state.waveSurfer.destroy()
  }
  const minPxPerSec = Number(dom.zoomRange?.value) || 120
  const waveSurfer = WaveSurferLib.create({
    container: "#waveform",
    waveColor: "#334155",
    progressColor: "#38bdf8",
    cursorColor: "#f97316",
    height: 200,
    autoCenter: true,
  })
  const regionsPlugin = waveSurfer.registerPlugin(
    RegionsPlugin.create({ dragSelection: false })
  )
  waveSurfer.on("ready", () => {
    waveSurfer.zoom(minPxPerSec)
    clampRegionsToDuration()
    refreshRegionTable()
  })
  waveSurfer.on("audioprocess", handleTimeUpdate)
  waveSurfer.on("finish", () => {
    state.skipGuardRegionId = null
  })
  setupRegionEvents(regionsPlugin)
  state.waveSurfer = waveSurfer
  state.regionsPlugin = regionsPlugin
}

function setupRegionEvents(plugin) {
  plugin.on("region-created", (region) => {
    if (!state.allowProgrammaticRegion && !state.altPressed) {
      region.remove()
      return
    }
    state.allowProgrammaticRegion = false
    if (!region.data) {
      region.data = {}
    }
    region.data.state = normalizeState(region.data.state || (state.altPressed ? "delete" : "undecided"))
    region.data.description = region.data.description || ""
    region.data.name = region.data.name || region.data.label || region.id
    finalizeRegion(region)
    refreshRegionTable()
  })
  plugin.on("region-updated", (region) => {
    finalizeRegion(region)
    refreshRegionTable()
  })
  plugin.on("region-removed", () => {
    refreshRegionTable()
  })
  plugin.on("region-clicked", (region, event) => {
    event?.preventDefault()
    setSelectedRegion(region)
    cycleRegionState(region)
  })
}

async function fetchStemList(force = false) {
  try {
    const data = await fetchJson(`${API_BASE}/list`)
    if (!Array.isArray(data.stems)) {
      throw new Error("服务器返回格式异常")
    }
    state.stems = data.stems
    renderStemList()
    if (force && state.selectedStem) {
      selectStem(state.selectedStem.stem)
      return
    }
    if (state.selectedStem && state.stems.some((item) => item.stem === state.selectedStem.stem)) {
      renderStemList()
      return
    }
    const preferredStem = state.queryStem || state.stems[0]?.stem
    state.queryStem = null
    if (preferredStem) {
      selectStem(preferredStem)
    }
  } catch (error) {
    console.error(error)
    showToast(`获取列表失败：${error.message || error}`, "error")
    renderStemList()
  }
}

function renderStemList() {
  if (!dom.stemList) return
  dom.stemList.innerHTML = ""
  if (!state.stems.length) {
    dom.stemEmpty?.classList.add("is-visible")
    return
  }
  dom.stemEmpty?.classList.remove("is-visible")
  state.stems.forEach((entry) => {
    const item = document.createElement("li")
    item.className = "stem-item"
    item.dataset.stem = entry.stem
    if (state.selectedStem && state.selectedStem.stem === entry.stem) {
      item.classList.add("is-active")
    }
    const title = document.createElement("div")
    title.className = "stem-item__name"
    title.textContent = entry.stem
    item.appendChild(title)
    const stats = document.createElement("div")
    stats.className = "stem-item__stats"
    const counts = [
      { icon: "🎧", value: entry.files?.audio?.length || 0 },
      { icon: "📄", value: entry.files?.edl?.length || 0 },
      { icon: "📝", value: entry.files?.csv?.length || 0 },
      { icon: "🔡", value: entry.files?.srt?.length || 0 },
      { icon: "📑", value: entry.files?.txt?.length || 0 },
    ]
    counts.forEach(({ icon, value }) => {
      const pill = document.createElement("span")
      pill.textContent = `${icon} ${value}`
      stats.appendChild(pill)
    })
    item.appendChild(stats)
    item.addEventListener("click", () => selectStem(entry.stem))
    dom.stemList.appendChild(item)
  })
}

async function selectStem(stem) {
  if (!stem) return
  const entry = state.stems.find((item) => item.stem === stem)
  state.selectedStem = entry || { stem, files: {} }
  state.currentStemOverride = stem
  renderStemList()
  await loadStem(entry)
}

async function loadStem(entry) {
  state.loadingStem = true
  updatePlaybackLabel("加载音频中…")
  clearAllRegions()
  updateRegionSummary()
  let audioToken = null
  let renderedToken = null
  let audioName = ""
  if (entry && Array.isArray(entry.files?.audio)) {
    const source = entry.files.audio.find((item) => item.kind === "source") || entry.files.audio[0]
    const rendered = entry.files.audio.find((item) => item.kind === "rendered")
    if (source) {
      audioToken = source.token
      audioName = source.name || ""
    }
    if (rendered) {
      renderedToken = rendered.token
    }
  }
  state.currentAudioToken = audioToken
  state.renderedAudioToken = renderedToken
  state.currentAudioFileName = audioName
  state.currentMarkerFileName = ""
  updateSelectionInfo()

  let regions = []
  let markerName = ""
  try {
    const edl = await fetchJson(`${API_BASE}/edl/${encodeURIComponent(state.selectedStem.stem)}`)
    regions = normalizeEdlSegments(edl)
    markerName = entry?.files?.edl?.[0]?.name || `${state.selectedStem.stem}.edl.json`
    if (typeof edl.stem === "string" && edl.stem.trim()) {
      state.currentStemOverride = edl.stem.trim()
    }
    if (typeof edl.source_audio === "string" && edl.source_audio.trim() && !state.currentAudioFileName) {
      state.currentAudioFileName = edl.source_audio.trim()
    }
  } catch (error) {
    try {
      const csv = await fetchJson(`${API_BASE}/csv/${encodeURIComponent(state.selectedStem.stem)}`)
      regions = (csv.regions || []).map((region) => ({
        start: Number(region.start) || 0,
        end: Number(region.end) || Number(region.start) || 0,
        state: normalizeState(region.state),
        description: region.description || "",
        label: region.label || region.id || "",
      }))
      markerName = csv.path ? csv.path.split("/").pop() || "" : `${state.selectedStem.stem}.csv`
    } catch (fallbackError) {
      console.warn("未找到 EDL/CSV，使用空白段落", fallbackError)
      regions = []
      markerName = ""
    }
  }

  clearAllRegions()
  addRegions(regions)
  state.currentMarkerFileName = markerName
  updateSelectionInfo()

  try {
    const srt = await fetchJson(`${API_BASE}/srt/${encodeURIComponent(state.selectedStem.stem)}`)
    state.srtItems = Array.isArray(srt.items)
      ? srt.items.map((item) => ({
          start: toSeconds(item.start),
          end: toSeconds(item.end),
          text: item.text || "",
        }))
      : []
  } catch (error) {
    state.srtItems = []
  }
  renderSrtList()

  if (state.currentAudioToken) {
    loadAudioFromToken(state.currentAudioToken, state.currentAudioFileName)
  } else if (state.audioObjectUrl) {
    loadLocalAudio(state.audioObjectUrl, state.currentAudioFileName)
  } else {
    updatePlaybackLabel("未找到音频，请手动导入或检查 audio-root")
  }
  // 默认使用前端软跳播预览，保持与页面预期一致
  setPlaybackMode("preview")
  state.loadingStem = false
}

function normalizeEdlSegments(edl) {
  if (!edl || !Array.isArray(edl.segments)) {
    return []
  }
  return edl.segments
    .map((segment) => {
      const start = Number(segment.start)
      const end = Number(segment.end)
      if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
        return null
      }
      const action = typeof segment.action === "string" ? segment.action : segment.keep ? "keep" : "delete"
      return {
        start,
        end,
        state: normalizeState(action === "drop" ? "delete" : action),
        description: segment.description || segment.desc || "",
        label: segment.label || segment.name || "",
      }
    })
    .filter(Boolean)
}

function loadAudioFromToken(token, label) {
  if (!token || !state.waveSurfer) {
    return
  }
  const sourceId = `token:${token}`
  if (state.currentSourceId === sourceId) {
    updatePlaybackLabel(label ? `${playbackModeLabel()} • ${label}` : playbackModeLabel())
    return
  }
  state.currentSourceId = sourceId
  try {
    state.waveSurfer.load(`${API_BASE}/audio/${token}`)
    state.waveSurfer.once("ready", () => {
      const minPxPerSec = Number(dom.zoomRange?.value) || 120
      state.waveSurfer.zoom(minPxPerSec)
      clampRegionsToDuration()
      updatePlaybackLabel(label ? `${playbackModeLabel()} • ${label}` : playbackModeLabel())
    })
  } catch (error) {
    showToast(`音频加载失败：${error.message}`, "error")
  }
}

function loadLocalAudio(objectUrl, label) {
  if (!state.waveSurfer) return
  if (state.audioObjectUrl && state.audioObjectUrl !== objectUrl) {
    try {
      URL.revokeObjectURL(state.audioObjectUrl)
    } catch (error) {
      console.warn("释放旧音频失败", error)
    }
  }
  state.audioObjectUrl = objectUrl
  state.currentSourceId = `local:${objectUrl}`
  try {
    state.waveSurfer.load(objectUrl)
    state.waveSurfer.once("ready", () => {
      const minPxPerSec = Number(dom.zoomRange?.value) || 120
      state.waveSurfer.zoom(minPxPerSec)
      clampRegionsToDuration()
      updatePlaybackLabel(label ? `${playbackModeLabel()} • ${label}` : playbackModeLabel())
    })
  } catch (error) {
    showToast(`本地音频加载失败：${error.message}`, "error")
  }
}

function setPlaybackMode(mode) {
  const normalized = ["preview", "original", "rendered"].includes(mode) ? mode : "preview"
  if (state.playbackMode === normalized) {
    return
  }
  state.playbackMode = normalized
  playbackTabs.forEach((tab) => {
    const isActive = tab.dataset.playback === normalized
    tab.classList.toggle("active", isActive)
    tab.setAttribute("aria-selected", isActive ? "true" : "false")
  })
  if (normalized === "rendered") {
    if (state.renderedAudioToken) {
      loadAudioFromToken(state.renderedAudioToken, "剪辑音频")
    } else {
      showToast("尚未生成剪辑音频，请先点击“生成剪辑音频”。", "info")
      state.playbackMode = "preview"
      setPlaybackMode("preview")
      return
    }
    state.skipDeletes = false
  } else {
    const label = normalized === "original" ? state.currentAudioFileName : state.selectedStem?.stem
    if (state.currentAudioToken) {
      loadAudioFromToken(state.currentAudioToken, label)
    } else if (state.audioObjectUrl) {
      loadLocalAudio(state.audioObjectUrl, label)
    }
    state.skipDeletes = normalized === "preview"
  }
  updateSkipToggle()
  updatePlaybackLabel()
}

function playbackModeLabel() {
  switch (state.playbackMode) {
    case "rendered":
      return "剪辑成品"
    case "original":
      return "原始音频"
    default:
      return "剪辑预览"
  }
}

function updatePlaybackLabel(extra) {
  if (!dom.playbackLabel) return
  if (extra) {
    dom.playbackLabel.textContent = extra
    return
  }
  const label = playbackModeLabel()
  dom.playbackLabel.textContent = state.currentAudioFileName
    ? `${label} • ${state.currentAudioFileName}`
    : label
}

function updateSkipToggle() {
  if (!dom.skipToggle) return
  dom.skipToggle.classList.toggle("active", state.skipDeletes)
  dom.skipToggle.disabled = state.playbackMode === "rendered"
}

function togglePlayback() {
  state.waveSurfer?.playPause()
}

function seekBy(delta) {
  if (!state.waveSurfer) return
  const current = state.waveSurfer.getCurrentTime?.() || 0
  const duration = state.waveSurfer.getDuration?.() || current
  const next = Math.max(0, Math.min(current + delta, duration))
  state.waveSurfer.setTime(next)
}

function handleTimeUpdate(time) {
  if (!Number.isFinite(time)) return
  if (state.playbackMode === "preview" && state.skipDeletes) {
    const target = getRegionsSorted().find(
      (region) => region.data.state === "delete" && time >= region.start && time < region.end - 0.02
    )
    if (target && state.skipGuardRegionId !== target.id) {
      state.skipGuardRegionId = target.id
      state.waveSurfer.setTime(target.end + 0.02)
      return
    }
    if (!target) {
      state.skipGuardRegionId = null
    }
  }
  highlightSubtitle(time)
}

function highlightSubtitle(time) {
  if (!Array.isArray(state.srtItems) || !state.srtItems.length || !dom.srtList) {
    return
  }
  const index = state.srtItems.findIndex((item) => time >= item.start && time <= item.end + 0.05)
  if (index === state.activeSubtitleIndex) {
    return
  }
  state.activeSubtitleIndex = index
  Array.from(dom.srtList.children).forEach((node, idx) => {
    node.classList.toggle("is-active", idx === index)
    if (idx === index) {
      node.scrollIntoView({ block: "nearest" })
    }
  })
}

function toggleSkipDeletes() {
  if (state.playbackMode === "rendered") {
    return
  }
  state.skipDeletes = !state.skipDeletes
  updateSkipToggle()
  showToast(state.skipDeletes ? "播放时跳过删除段" : "播放将包含删除段", "info")
}

function handleZoomChange() {
  if (!state.waveSurfer) return
  const value = Number(dom.zoomRange.value) || 120
  state.waveSurfer.zoom(value)
}

function handleKeyDown(event) {
  if (event.key === "Alt") {
    if (!state.altPressed) {
      state.altPressed = true
      enableAltDrag()
    }
    return
  }
  if (event.target && ["INPUT", "TEXTAREA"].includes(event.target.tagName)) {
    return
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "s") {
    event.preventDefault()
    exportEdl()
    return
  }
  switch (event.key) {
    case " ":
      event.preventDefault()
      togglePlayback()
      break
    case "d":
    case "D":
      cycleRegionState(getSelectedRegion())
      break
    case "Delete":
    case "Backspace":
      const region = getSelectedRegion()
      if (region) {
        region.remove()
      }
      break
    case "ArrowLeft":
      focusNeighborRegion(-1)
      break
    case "ArrowRight":
      focusNeighborRegion(1)
      break
  }
}

function handleKeyUp(event) {
  if (event.key === "Alt") {
    state.altPressed = false
    disableAltDrag()
  }
}

function focusNeighborRegion(direction) {
  const regions = getRegionsSorted()
  if (!regions.length) return
  const current = getSelectedRegion()
  let index = current ? regions.findIndex((region) => region.id === current.id) : -1
  index = Math.max(0, Math.min(regions.length - 1, index + direction))
  const target = regions[index]
  if (target) {
    setSelectedRegion(target)
    state.waveSurfer?.setTime(target.start)
  }
}

function enableAltDrag() {
  if (!state.regionsPlugin || state.dragSelectionHandle) return
  state.dragSelectionHandle = state.regionsPlugin.enableDragSelection(
    { color: stateColor("delete"), drag: true, resize: true },
    3
  )
}

function disableAltDrag() {
  if (state.dragSelectionHandle) {
    state.dragSelectionHandle()
    state.dragSelectionHandle = null
  }
}

function clearAllRegions() {
  state.selectedRegionId = null
  state.skipGuardRegionId = null
  state.regionsPlugin?.clearRegions()
  if (dom.regionTableBody) {
    dom.regionTableBody.innerHTML = ""
  }
  updateRegionSummary()
}

function addRegions(specs) {
  if (!Array.isArray(specs)) return
  ensureWaveSurfer()
  const sorted = [...specs]
    .map((spec, index) => ({ ...spec, __index: index }))
    .sort((a, b) => (a.start ?? 0) - (b.start ?? 0))
  for (const spec of sorted) {
    let start = Number(spec.start ?? 0)
    let end = Number(spec.end ?? start)
    if (!Number.isFinite(start)) {
      continue
    }
    if (!Number.isFinite(end) || end <= start) {
      end = start + 0.01
    }
    const description = spec.description || spec.desc || ""
    const label = spec.label || spec.name || ""
    const normalizedState = normalizeState(spec.state)
    state.allowProgrammaticRegion = true
    const region = state.regionsPlugin.addRegion({
      id: spec.id,
      start,
      end,
      drag: true,
      resize: true,
      color: stateColor(normalizedState),
      data: {
        state: normalizedState,
        description,
        name: label,
      },
    })
    finalizeRegion(region)
  }
  refreshRegionTable()
}

function ensureWaveSurfer() {
  if (!state.waveSurfer) {
    createWaveSurfer()
  }
}

function getRegionsSorted() {
  if (!state.regionsPlugin) return []
  return [...state.regionsPlugin.getRegions()].sort((a, b) => a.start - b.start)
}

function refreshRegionTable() {
  if (!dom.regionTableBody) return
  const regions = getRegionsSorted()
  dom.regionTableBody.innerHTML = ""
  regions.forEach((region, index) => {
    const row = document.createElement("tr")
    row.dataset.regionId = region.id
    if (region.id === state.selectedRegionId) {
      row.classList.add("is-selected")
    }
    row.appendChild(createCell(String(index + 1)))
    const stateCell = document.createElement("td")
    const pill = document.createElement("span")
    pill.className = `region-pill ${region.data.state}`
    pill.textContent = STATE_LABELS[region.data.state]
    stateCell.appendChild(pill)
    row.appendChild(stateCell)
    row.appendChild(createCell(secondsToHms(region.start)))
    row.appendChild(createCell(secondsToHms(region.end)))
    row.appendChild(createCell(secondsToHms(Math.max(0, region.end - region.start))))
    row.appendChild(createCell(region.data.description || region.data.name || ""))
    row.addEventListener("click", () => {
      setSelectedRegion(region)
    })
    dom.regionTableBody.appendChild(row)
  })
  updateRegionSummary()
}

function createCell(text) {
  const td = document.createElement("td")
  td.textContent = text
  return td
}

function setSelectedRegion(region) {
  state.selectedRegionId = region?.id || null
  getRegionsSorted().forEach((item) => {
    if (item.element) {
      item.element.classList.toggle("is-selected", item.id === state.selectedRegionId)
    }
  })
  refreshRegionTable()
}

function getSelectedRegion() {
  if (!state.selectedRegionId) return null
  return getRegionsSorted().find((region) => region.id === state.selectedRegionId) || null
}

function cycleRegionState(region) {
  if (!region) return
  const currentIndex = REGION_STATES.indexOf(region.data.state)
  const nextIndex = currentIndex === -1 ? 0 : (currentIndex + 1) % REGION_STATES.length
  region.data.state = REGION_STATES[nextIndex]
  finalizeRegion(region)
  refreshRegionTable()
}

function applyStateToAll(nextState) {
  const normalized = normalizeState(nextState)
  if (!normalized) return
  for (const region of getRegionsSorted()) {
    region.data.state = normalized
    finalizeRegion(region)
  }
  refreshRegionTable()
}

function finalizeRegion(region) {
  const stateKey = normalizeState(region.data?.state)
  region.data.state = stateKey
  region.setOptions({ color: stateColor(stateKey) })
  if (region.element) {
    region.element.dataset.state = stateKey
    region.element.classList.toggle("is-selected", region.id === state.selectedRegionId)
    region.element.title = buildRegionTooltip(region)
  }
}

function buildRegionTooltip(region) {
  const start = secondsToHms(region.start)
  const end = secondsToHms(region.end)
  const duration = secondsToHms(Math.max(0, region.end - region.start))
  const description = region.data.description ? `\n${region.data.description}` : ""
  return `${STATE_LABELS[region.data.state]} ${start} → ${end} (${duration})${description}`
}

function clampRegionsToDuration() {
  if (!state.waveSurfer || !state.regionsPlugin) return
  const duration = state.waveSurfer.getDuration?.()
  if (!Number.isFinite(duration) || duration <= 0) return
  let changed = false
  for (const region of getRegionsSorted()) {
    if (region.start < 0 || region.end > duration) {
      const start = Math.max(0, Math.min(region.start, duration))
      const end = Math.max(start + 0.01, Math.min(region.end, duration))
      region.setOptions({ start, end })
      finalizeRegion(region)
      changed = true
    }
  }
  if (changed) {
    refreshRegionTable()
  }
}

function updateRegionSummary() {
  if (!dom.regionSummary) return
  const regions = getRegionsSorted()
  if (!regions.length) {
    dom.regionSummary.textContent = "暂无区段"
    return
  }
  const total = regions.length
  const keepCount = regions.filter((region) => region.data.state !== "delete").length
  const duration = regions
    .filter((region) => region.data.state !== "delete")
    .reduce((sum, region) => sum + Math.max(0, region.end - region.start), 0)
  dom.regionSummary.textContent = `共 ${total} 段 / 保留 ${keepCount} 段 / ${secondsToHms(duration)}`
}

function buildExportRegions() {
  return getRegionsSorted().map((region) => ({
    start: roundSeconds(region.start),
    end: roundSeconds(region.end),
    state: region.data.state,
    label: region.data.name || "",
    description: region.data.description || "",
  }))
}

async function exportEdl() {
  const stem = buildStem()
  if (!stem) {
    showToast("请先选择或导入标记文件。", "warning")
    return
  }
  const regions = buildExportRegions()
  try {
    const payload = await fetchJson(`${API_BASE}/export/edl`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stem, regions }),
    })
    showToast(`EDL 已保存：${payload.path}`, "success")
    fetchStemList(true)
  } catch (error) {
    showToast(`导出 EDL 失败：${error.message || error}`, "error")
  }
}

async function exportAuditionCsv() {
  const stem = buildStem()
  if (!stem) {
    showToast("请先选择或导入标记文件。", "warning")
    return
  }
  const regions = buildExportRegions()
  try {
    const payload = await fetchJson(`${API_BASE}/export/csv`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stem, regions, dialect: "audition" }),
    })
    showToast(`Audition CSV 已保存：${payload.path}`, "success")
    fetchStemList(true)
  } catch (error) {
    showToast(`导出 CSV 失败：${error.message || error}`, "error")
  }
}

async function requestRender(force = false) {
  if (!state.selectedStem) {
    showToast("请先选择一个 stem。", "warning")
    return
  }
  if (state.pendingRender) {
    return
  }
  if (state.renderedAudioToken && !force) {
    const confirmed = window.confirm("检测到已有剪辑音频，是否重新渲染并覆盖？(Shift+点击可直接强制渲染)")
    if (!confirmed) {
      return
    }
    force = true
  }
  state.pendingRender = true
  if (dom.renderButton) {
    dom.renderButton.textContent = "渲染中..."
    dom.renderButton.disabled = true
  }
  try {
    const payload = await fetchJson(`${API_BASE}/render`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stem: state.selectedStem.stem, force, format: "wav" }),
    })
    state.renderedAudioToken = payload.path_token || null
    showToast(payload.skipped ? "剪辑音频已存在，未重复渲染。" : "剪辑音频渲染完成！", "success")
    if (!payload.skipped && payload.path_token) {
      loadAudioFromToken(payload.path_token, "剪辑音频")
      setPlaybackMode("rendered")
    }
    fetchStemList(true)
  } catch (error) {
    showToast(`渲染失败：${error.message || error}`, "error")
  } finally {
    state.pendingRender = false
    if (dom.renderButton) {
      dom.renderButton.textContent = "生成剪辑音频"
      dom.renderButton.disabled = false
    }
  }
}

function normalizeState(value) {
  const lower = (value || "").toString().trim().toLowerCase()
  if (REGION_STATES.includes(lower)) {
    return lower
  }
  if (["remove", "cut", "drop", "delete"].includes(lower)) {
    return "delete"
  }
  if (["keep", "kept", "retain"].includes(lower)) {
    return "keep"
  }
  if (["undecided", "todo", "pending", "unknown"].includes(lower)) {
    return "undecided"
  }
  return "undecided"
}

function stateColor(name) {
  return REGION_COLORS[name] || REGION_COLORS.undecided
}

function buildStem() {
  if (state.currentStemOverride) {
    return state.currentStemOverride
  }
  if (state.selectedStem?.stem) {
    return state.selectedStem.stem
  }
  return ""
}

function updateSelectionInfo() {
  if (dom.currentStemTitle) {
    dom.currentStemTitle.textContent = state.selectedStem?.stem || "请选择 Stem"
  }
  if (dom.currentStem) {
    dom.currentStem.textContent = buildStem() || "-"
  }
  if (dom.currentAudio) {
    dom.currentAudio.textContent = state.currentAudioFileName || "未选择"
  }
  if (dom.currentMarker) {
    dom.currentMarker.textContent = state.currentMarkerFileName || "未选择"
  }
}

function secondsToHms(value) {
  const millis = Math.max(0, Math.round(Number(value || 0) * 1000))
  const hours = Math.floor(millis / 3_600_000)
  const minutes = Math.floor((millis % 3_600_000) / 60_000)
  const seconds = Math.floor((millis % 60_000) / 1000)
  const ms = millis % 1000
  return `${hours.toString().padStart(2, "0")}:${minutes
    .toString()
    .padStart(2, "0")}:${seconds.toString().padStart(2, "0")}.${ms
    .toString()
    .padStart(3, "0")}`
}

function roundSeconds(value) {
  return Math.round(Number(value || 0) * 1000) / 1000
}

function toSeconds(value) {
  if (typeof value === "number") {
    return value
  }
  const text = (value || "").toString().trim()
  if (!text) return 0
  if (/^\d+(\.\d+)?$/.test(text)) {
    return parseFloat(text)
  }
  const match = text.match(/^(\d+):(\d{2}):(\d{2})(?:,(\d{1,3}))?$/)
  if (match) {
    const hours = parseInt(match[1], 10)
    const minutes = parseInt(match[2], 10)
    const seconds = parseInt(match[3], 10)
    const milliseconds = parseInt(match[4] || "0", 10)
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000
  }
  return 0
}

function renderSrtList() {
  if (!dom.srtList) return
  dom.srtList.innerHTML = ""
  if (!state.srtItems.length) {
    const empty = document.createElement("li")
    empty.className = "srt-line"
    empty.textContent = "未加载字幕"
    dom.srtList.appendChild(empty)
    return
  }
  state.srtItems.forEach((item) => {
    const li = document.createElement("li")
    li.className = "srt-line"
    li.textContent = `${secondsToHms(item.start)} → ${secondsToHms(item.end)}  ${item.text}`
    dom.srtList.appendChild(li)
  })
  state.activeSubtitleIndex = -1
}

async function fetchJson(url, options) {
  const response = await fetch(url, options)
  if (!response.ok) {
    let message = response.statusText
    try {
      const payload = await response.json()
      message = payload?.detail || payload?.error || message
    } catch (error) {
      // ignore
    }
    throw new Error(message || `请求失败 (${response.status})`)
  }
  return response.json()
}

function setupDragAndDrop() {
  if (!dom.dropzone) return
  let dragDepth = 0
  window.addEventListener("dragenter", (event) => {
    if (!dragEventHasFiles(event)) return
    dragDepth += 1
    dom.dropzone.classList.add("dropzone--hover")
  })
  window.addEventListener("dragleave", (event) => {
    if (!dragEventHasFiles(event)) return
    dragDepth = Math.max(0, dragDepth - 1)
    if (dragDepth === 0) {
      dom.dropzone.classList.remove("dropzone--hover")
    }
  })
  window.addEventListener("dragover", (event) => {
    if (event.dataTransfer) {
      event.dataTransfer.dropEffect = "copy"
    }
    event.preventDefault()
  })
  window.addEventListener("drop", (event) => {
    event.preventDefault()
    dom.dropzone.classList.remove("dropzone--hover")
    dragDepth = 0
    const files = event.dataTransfer?.files
    if (!files || !files.length) return
    for (const file of files) {
      routeFile(file)
    }
  })
  dom.dropzone.addEventListener("click", () => {
    dom.pickMarkers?.click()
  })
}

function dragEventHasFiles(event) {
  const types = event.dataTransfer?.types
  if (!types) return false
  if (typeof types.includes === "function") {
    return types.includes("Files")
  }
  if (typeof types.contains === "function") {
    return types.contains("Files")
  }
  return false
}

function routeFile(file) {
  const name = file.name.toLowerCase()
  if (/\.(wav|m4a|mp3|flac|aac|ogg|wma)$/.test(name)) {
    handleAudioFile(file)
    return
  }
  if (/\.(csv|json|srt)$/.test(name)) {
    handleMarkerFile(file)
    return
  }
  showToast(`不支持的文件类型: ${file.name}`, "error")
}

async function handleAudioFile(file) {
  try {
    if (state.audioObjectUrl) {
      URL.revokeObjectURL(state.audioObjectUrl)
    }
  } catch (error) {
    console.warn("释放旧音频 URL 失败", error)
  }
  const objectUrl = URL.createObjectURL(file)
  state.audioObjectUrl = objectUrl
  state.currentAudioFileName = file.name
  state.currentAudioToken = null
  state.currentSourceId = ""
  updateSelectionInfo()
  loadLocalAudio(objectUrl, file.name)
  showToast(`已加载音频：${file.name}`, "success")
}

async function handleMarkerFile(file) {
  try {
    clearAllRegions()
    const text = await file.text()
    const lower = file.name.toLowerCase()
    if (lower.endsWith(".csv")) {
      const rows = parseCsv(text)
      const regions = rowsToRegionsFromCsv(rows)
      addRegions(regions)
    } else if (lower.endsWith(".json")) {
      const edl = parseEdl(text)
      if (edl.stem) {
        state.currentStemOverride = edl.stem
      }
      addRegions(edl.segments)
    } else if (lower.endsWith(".srt")) {
      const items = parseSrt(text)
      const regions = srtToRegions(items)
      addRegions(regions)
    } else {
      throw new Error("未知的标记格式")
    }
    state.currentMarkerFileName = file.name
    updateSelectionInfo()
    showToast(`已加载标记：共 ${getRegionsSorted().length} 段`, "success")
  } catch (error) {
    showToast(`标记加载失败: ${error.message}`, "error")
  }
}

function parseCsv(text) {
  const content = text.replace(/^\ufeff/, "")
  const rows = []
  for (const line of content.split(/\r?\n/)) {
    if (!line.trim()) {
      continue
    }
    const parts = []
    let cell = ""
    let inQuotes = false
    for (let i = 0; i < line.length; i += 1) {
      const char = line[i]
      if (char === '"') {
        if (inQuotes && line[i + 1] === '"') {
          cell += '"'
          i += 1
        } else {
          inQuotes = !inQuotes
        }
      } else if (char === "," && !inQuotes) {
        parts.push(cell)
        cell = ""
      } else {
        cell += char
      }
    }
    parts.push(cell)
    rows.push(parts)
  }
  if (!rows.length) {
    throw new Error("CSV 内容为空")
  }
  return rows
}

function rowsToRegionsFromCsv(rows) {
  const header = rows[0].map(normalizeHeader)
  const body = rows.slice(1)
  const indexMap = {
    name: findColumn(header, ["name", "marker", "label", "title"]),
    start: findColumn(header, ["start", "in", "starttime"]),
    duration: findColumn(header, ["duration", "length"]),
    end: findColumn(header, ["end", "out"]),
    description: findColumn(header, ["description", "comment", "notes"]),
    type: findColumn(header, ["type", "state"]),
  }
  if (indexMap.start === -1) {
    throw new Error("CSV 表头缺少 Start/In 列")
  }
  const regions = []
  body.forEach((row, rowIndex) => {
    if (!row || row.every((cell) => !cell || !cell.trim())) {
      return
    }
    const startText = row[indexMap.start] ?? ""
    const endText = indexMap.end !== -1 ? row[indexMap.end] ?? "" : ""
    const durationText = indexMap.duration !== -1 ? row[indexMap.duration] ?? "" : ""
    const start = parseTimeAnySafe(startText, rowIndex)
    let end = null
    if (indexMap.end !== -1 && endText) {
      end = parseTimeAnySafe(endText, rowIndex)
    }
    if ((end === null || Number.isNaN(end)) && durationText) {
      const duration = parseTimeAnySafe(durationText, rowIndex)
      end = start + duration
    }
    if (end === null || Number.isNaN(end)) {
      throw new Error(`CSV 第 ${rowIndex + 2} 行缺少时长或结束时间`)
    }
    const name = indexMap.name !== -1 ? (row[indexMap.name] || "").trim() : ""
    const description = indexMap.description !== -1 ? (row[indexMap.description] || "").trim() : ""
    const typeText = indexMap.type !== -1 ? (row[indexMap.type] || "").trim() : ""
    regions.push({
      start,
      end,
      state: normalizeState(typeText) || "undecided",
      description,
      label: name,
    })
  })
  return regions
}

function parseTimeAnySafe(value, rowIndex) {
  try {
    return parseTimeAny(value)
  } catch (error) {
    throw new Error(`第 ${rowIndex + 2} 行 ${error.message}`)
  }
}

function parseTimeAny(value) {
  if (typeof value === "number") {
    return value
  }
  if (value === null || value === undefined) {
    throw new Error("缺少时间值")
  }
  const text = value.toString().trim()
  if (!text) {
    throw new Error("缺少时间值")
  }
  if (/^\d+(\.\d+)?$/.test(text)) {
    return parseFloat(text)
  }
  const match = text.match(/^(\d+):([0-5]?\d):([0-5]?\d(?:\.\d+)?)$/)
  if (match) {
    const hours = parseInt(match[1], 10)
    const minutes = parseInt(match[2], 10)
    const seconds = parseFloat(match[3])
    return hours * 3600 + minutes * 60 + seconds
  }
  throw new Error(`无法解析时间: ${text}`)
}

function findColumn(header, aliases) {
  return header.findIndex((cell) => aliases.includes(cell))
}

function normalizeHeader(cell) {
  return (cell || "").toString().trim().replace(/\s+/g, "").toLowerCase()
}

function parseEdl(text) {
  const payload = JSON.parse(text)
  const segments = []
  const source = Array.isArray(payload.segments)
    ? payload.segments
    : Array.isArray(payload.regions)
    ? payload.regions
    : []
  for (const item of source) {
    if (!item) continue
    const start = Number(item.start)
    const end = Number(item.end)
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue
    segments.push({
      start,
      end,
      state: normalizeState(item.state || item.type),
      description: item.description || item.desc || "",
      label: item.label || item.name || "",
    })
  }
  return {
    version: payload.version || 1,
    stem: payload.stem || "",
    source_audio: payload.source_audio || payload.audio || "",
    segments,
  }
}

function parseSrt(text) {
  const normalized = text.replace(/^\ufeff/, "").replace(/\r\n/g, "\n")
  const blocks = normalized.split(/\n\n+/)
  const items = []
  for (const block of blocks) {
    const lines = block.trim().split(/\n/).filter(Boolean)
    if (lines.length < 2) continue
    const timeLine = lines.find((line) => line.includes("--"))
    if (!timeLine) continue
    const [startText, endText] = timeLine.split("-->")
    if (!endText) continue
    const start = parseTimeAny(startText.replace(/,/g, "."))
    const end = parseTimeAny(endText.replace(/,/g, "."))
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) continue
    const textContent = lines.slice(lines.indexOf(timeLine) + 1).join("\n")
    items.push({ start, end, text: textContent })
  }
  return items
}

function srtToRegions(items) {
  return items.map((item, index) => ({
    start: item.start,
    end: item.end,
    state: "undecided",
    description: item.text,
    label: `SRT ${index + 1}`,
  }))
}

function showToast(message, type = "info") {
  if (!dom.toastContainer) return
  const toast = document.createElement("div")
  toast.className = "toast"
  const typeClass = {
    success: "toast--success",
    error: "toast--error",
    info: "toast--info",
    warning: "toast--warning",
  }[type]
  if (typeClass) {
    toast.classList.add(typeClass)
  }
  const messageSpan = document.createElement("span")
  messageSpan.className = "toast__message"
  messageSpan.textContent = message
  toast.appendChild(messageSpan)
  dom.toastContainer.appendChild(toast)
  requestAnimationFrame(() => {
    toast.style.opacity = "1"
    toast.style.transform = "translateY(0)"
  })
  setTimeout(() => {
    toast.style.opacity = "0"
    toast.style.transform = "translateY(-10px)"
    setTimeout(() => toast.remove(), 300)
  }, 4000)
}

window.addEventListener("beforeunload", () => {
  if (state.audioObjectUrl) {
    try {
      URL.revokeObjectURL(state.audioObjectUrl)
    } catch (error) {
      console.warn("释放音频 URL 失败", error)
    }
  }
})

