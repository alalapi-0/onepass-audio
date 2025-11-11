const WaveSurferLib = window.WaveSurfer
const RegionsPlugin = WaveSurferLib?.Regions

const REGION_STATES = ["keep", "delete", "undecided"]
const REGION_COLORS = {
  keep: "rgba(34,197,94,0.35)",
  delete: "rgba(239,68,68,0.35)",
  undecided: "rgba(148,163,184,0.4)",
}
const STATE_LABELS = {
  keep: "保留",
  delete: "删除",
  undecided: "未决",
}
const CSV_HEADER = ["Name", "Start", "Duration", "Type", "Description"]

const state = {
  waveSurfer: null,
  regionsPlugin: null,
  dragSelectionHandle: null,
  altPressed: false,
  allowProgrammaticRegion: false,
  skipDeletes: true,
  selectedRegionId: null,
  skipGuardRegionId: null,
  currentAudioFileName: "",
  currentMarkerFileName: "",
  currentStemOverride: "",
  audioObjectUrl: null,
}

const dom = {
  toastContainer: document.getElementById("toast-container"),
  pickAudio: document.getElementById("pick-audio"),
  btnPickAudio: document.getElementById("btn-pick-audio"),
  pickMarkers: document.getElementById("pick-markers"),
  btnPickMarkers: document.getElementById("btn-pick-markers"),
  dropzone: document.getElementById("dropzone"),
  currentAudio: document.getElementById("current-audio"),
  currentMarker: document.getElementById("current-marker"),
  currentStem: document.getElementById("current-stem"),
  playToggle: document.getElementById("play-toggle"),
  seekBack: document.getElementById("seek-back"),
  seekForward: document.getElementById("seek-forward"),
  skipToggle: document.getElementById("skip-toggle"),
  zoomRange: document.getElementById("zoom-range"),
  exportEdl: document.getElementById("export-edl"),
  exportMarkers: document.getElementById("export-markers"),
  regionTableBody: document.getElementById("region-tbody"),
}

if (!WaveSurferLib || !RegionsPlugin) {
  showToast("缺少 WaveSurfer 依赖，请确认 vendor/ 目录存在。", "error")
}

function init() {
  attachImportEvents()
  attachControlEvents()
  attachKeyboardEvents()
  setupDragAndDrop()
  createWaveSurfer()
  updateSelectionInfo()
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
    waveColor: "#475569",
    progressColor: "#2563eb",
    cursorColor: "#f97316",
    height: 200,
    minPxPerSec,
    autoCenter: true,
  })
  const regionsPlugin = waveSurfer.registerPlugin(RegionsPlugin.create({}))
  state.waveSurfer = waveSurfer
  state.regionsPlugin = regionsPlugin
  setupRegionEvents(regionsPlugin)
  waveSurfer.on("ready", () => {
    waveSurfer.zoom(minPxPerSec)
    clampRegionsToDuration()
    refreshRegionTable()
  })
  waveSurfer.on("timeupdate", handleTimeUpdate)
  waveSurfer.on("finish", () => {
    state.skipGuardRegionId = null
  })
  if (state.audioObjectUrl) {
    waveSurfer.load(state.audioObjectUrl)
  }
}

function attachImportEvents() {
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
}

function attachControlEvents() {
  dom.playToggle?.addEventListener("click", togglePlayback)
  dom.seekBack?.addEventListener("click", () => seekBy(-5))
  dom.seekForward?.addEventListener("click", () => seekBy(5))
  dom.skipToggle?.addEventListener("click", toggleSkipDeletes)
  dom.zoomRange?.addEventListener("input", handleZoomChange)
  dom.exportEdl?.addEventListener("click", exportEdl)
  dom.exportMarkers?.addEventListener("click", exportAuditionCsv)
  document.querySelectorAll("[data-bulk]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = normalizeState(button.dataset.bulk)
      if (next) {
        applyStateToAll(next)
      }
    })
  })
}

function attachKeyboardEvents() {
  window.addEventListener("keydown", handleKeyDown)
  window.addEventListener("keyup", handleKeyUp)
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

function routeFile(file) {
  const name = file.name.toLowerCase()
  if (/\.(wav|m4a|mp3|flac)$/.test(name)) {
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
  state.currentStemOverride = ""
  updateSelectionInfo()
  ensureWaveSurfer()
  try {
    state.waveSurfer.load(objectUrl)
    state.waveSurfer.once("ready", () => {
      const minPxPerSec = Number(dom.zoomRange?.value) || 120
      state.waveSurfer.zoom(minPxPerSec)
      clampRegionsToDuration()
      showToast(`已加载音频：${file.name}`, "success")
    })
  } catch (error) {
    showToast(`音频加载失败: ${error.message}`, "error")
  }
}

async function handleMarkerFile(file) {
  try {
    state.currentStemOverride = ""
    clearAllRegions()
    const text = await file.text()
    const lower = file.name.toLowerCase()
    if (lower.endsWith(".csv")) {
      const rows = parseCsv(text)
      const regions = rowsToRegionsFromCsv(rows)
      addRegions(regions)
    } else if (lower.endsWith(".json")) {
      const edl = parseEdl(text)
      if (!state.currentAudioFileName && edl.source_audio) {
        showToast(`EDL 指定了音频 ${edl.source_audio}，请导入对应音频。`, "warning")
      }
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

function ensureWaveSurfer() {
  if (!state.waveSurfer) {
    createWaveSurfer()
  }
}

function clearAllRegions() {
  state.selectedRegionId = null
  state.skipGuardRegionId = null
  if (state.regionsPlugin) {
    state.regionsPlugin.clearRegions()
  }
  if (dom.regionTableBody) {
    dom.regionTableBody.innerHTML = ""
  }
}

function addRegions(specs) {
  if (!Array.isArray(specs)) return
  ensureWaveSurfer()
  const duration = state.waveSurfer?.getDuration?.()
  const hasDuration = Number.isFinite(duration) && duration > 0
  const sorted = [...specs]
    .map((spec, index) => ({ ...spec, __index: index }))
    .sort((a, b) => (a.start ?? 0) - (b.start ?? 0))
  for (const spec of sorted) {
    let start = Number(spec.start ?? 0)
    let end = Number(spec.end ?? start)
    if (!Number.isFinite(start)) {
      throw new Error(`无法解析开始时间 (第 ${spec.__index + 1} 行)`) 
    }
    if (!Number.isFinite(end)) {
      end = start
    }
    if (end <= start) {
      end = start + 0.01
    }
    if (hasDuration) {
      start = Math.max(0, Math.min(start, duration))
      end = Math.max(start + 0.01, Math.min(end, duration))
    } else {
      start = Math.max(0, start)
      end = Math.max(start + 0.01, end)
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

function handleTimeUpdate(time) {
  if (!state.skipDeletes) return
  const target = getRegionsSorted().find(
    (region) => region.data.state === "delete" && time >= region.start && time < region.end - 0.01,
  )
  if (!target) {
    state.skipGuardRegionId = null
    return
  }
  if (state.skipGuardRegionId === target.id) {
    return
  }
  state.skipGuardRegionId = target.id
  state.waveSurfer.setTime(target.end + 0.01)
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

function toggleSkipDeletes() {
  state.skipDeletes = !state.skipDeletes
  if (dom.skipToggle) {
    dom.skipToggle.classList.toggle("active", state.skipDeletes)
  }
  showToast(state.skipDeletes ? "播放时跳过删除段" : "播放包含删除段", "info")
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
  if (event.code === "Space") {
    event.preventDefault()
    togglePlayback()
  } else if (event.key === "d" || event.key === "D") {
    event.preventDefault()
    const region = getRegionsSorted().find((item) => item.id === state.selectedRegionId)
    if (region) {
      cycleRegionState(region)
    }
  } else if (event.key === "Delete") {
    event.preventDefault()
    const region = getRegionsSorted().find((item) => item.id === state.selectedRegionId)
    if (region) {
      region.remove()
      state.selectedRegionId = null
    }
  } else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
    event.preventDefault()
    const regions = getRegionsSorted()
    if (!regions.length) return
    const currentIndex = regions.findIndex((item) => item.id === state.selectedRegionId)
    let nextIndex = currentIndex
    if (event.key === "ArrowRight") {
      nextIndex = currentIndex >= 0 ? Math.min(currentIndex + 1, regions.length - 1) : 0
    } else {
      nextIndex = currentIndex > 0 ? currentIndex - 1 : 0
    }
    setSelectedRegion(regions[nextIndex])
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault()
    exportEdl()
  }
}

function handleKeyUp(event) {
  if (event.key === "Alt") {
    state.altPressed = false
    disableAltDrag()
  }
}

function enableAltDrag() {
  if (!state.regionsPlugin || state.dragSelectionHandle) return
  state.dragSelectionHandle = state.regionsPlugin.enableDragSelection(
    { color: stateColor("delete"), drag: true, resize: true },
    3,
  )
}

function disableAltDrag() {
  if (state.dragSelectionHandle) {
    state.dragSelectionHandle()
    state.dragSelectionHandle = null
  }
}

function exportEdl() {
  const edl = buildEdl()
  const blob = new Blob([JSON.stringify(edl, null, 2)], { type: "application/json" })
  const filename = `${buildStem() || "export"}.edited.edl.json`
  saveBlob(blob, filename)
  showToast(`已导出 ${filename}`, "success")
}

function exportAuditionCsv() {
  const csv = buildAuditionCsv()
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" })
  const filename = `${buildStem() || "export"}.edited.audition_markers.csv`
  saveBlob(blob, filename)
  showToast(`已导出 ${filename}`, "success")
}

function buildEdl() {
  const segments = getRegionsSorted().map((region) => ({
    start: roundSeconds(region.start),
    end: roundSeconds(region.end),
    state: region.data.state,
    label: region.data.name || "",
    desc: region.data.description || "",
  }))
  return {
    version: 1,
    stem: buildStem(),
    source_audio: state.currentAudioFileName || "",
    segments,
  }
}

function buildAuditionCsv() {
  const rows = [CSV_HEADER]
  for (const region of getRegionsSorted()) {
    const name = region.data.name || region.id
    const start = formatSeconds(region.start)
    const duration = formatSeconds(Math.max(0, region.end - region.start))
    const type = region.data.state || "undecided"
    const description = region.data.description || ""
    rows.push([name, start, duration, type, description])
  }
  return rows.map((row) => row.map(escapeCsvCell).join(",")).join("\r\n")
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  link.style.display = "none"
  document.body.appendChild(link)
  link.click()
  setTimeout(() => {
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }, 0)
}

function parseCsv(text) {
  const content = text.replace(/^\ufeff/, "")
  const rows = []
  let current = []
  let field = ""
  let inQuotes = false
  for (let i = 0; i < content.length; i += 1) {
    const char = content[i]
    if (char === '"') {
      if (inQuotes && content[i + 1] === '"') {
        field += '"'
        i += 1
      } else {
        inQuotes = !inQuotes
      }
    } else if (char === "," && !inQuotes) {
      current.push(field)
      field = ""
    } else if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && content[i + 1] === "\n") {
        i += 1
      }
      current.push(field)
      rows.push(current)
      current = []
      field = ""
    } else {
      field += char
    }
  }
  if (field.length || current.length) {
    current.push(field)
    rows.push(current)
  }
  return rows.filter((row) => row.some((cell) => cell.trim().length > 0))
}

function rowsToRegionsFromCsv(rows) {
  if (!rows.length) {
    throw new Error("CSV 内容为空")
  }
  const header = rows[0].map(normalizeHeader)
  const body = rows.slice(1)
  const indexMap = {
    name: findColumn(header, ["name", "marker", "label"]),
    start: findColumn(header, ["start", "in"]),
    duration: findColumn(header, ["duration", "length"]),
    end: findColumn(header, ["end"]),
    description: findColumn(header, ["description", "comment"]),
    type: findColumn(header, ["type"]),
  }
  if (indexMap.start === -1) {
    throw new Error("CSV 表头缺少 Start/In 列")
  }
  if (indexMap.duration === -1 && indexMap.end === -1) {
    throw new Error("CSV 表头缺少 Duration/Length 或 End 列")
  }
  const regions = []
  body.forEach((row, rowIndex) => {
    const startText = row[indexMap.start] ?? ""
    let endText = indexMap.end !== -1 ? row[indexMap.end] ?? "" : ""
    const durationText = indexMap.duration !== -1 ? row[indexMap.duration] ?? "" : ""
    const name = indexMap.name !== -1 ? (row[indexMap.name] || "").trim() : ""
    const description = indexMap.description !== -1 ? (row[indexMap.description] || "").trim() : ""
    const typeText = indexMap.type !== -1 ? (row[indexMap.type] || "").trim() : ""
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

function normalizeState(value) {
  const lower = (value || "").toString().trim().toLowerCase()
  if (REGION_STATES.includes(lower)) {
    return lower
  }
  if (lower === "remove" || lower === "cut" || lower === "delete") {
    return "delete"
  }
  if (lower === "keep" || lower === "kept" || lower === "retain") {
    return "keep"
  }
  if (lower === "undecided" || lower === "todo" || lower === "pending") {
    return "undecided"
  }
  return "undecided"
}

function stateColor(name) {
  return REGION_COLORS[name] || REGION_COLORS.undecided
}

function updateSelectionInfo() {
  if (dom.currentAudio) {
    dom.currentAudio.textContent = state.currentAudioFileName || "未选择"
  }
  if (dom.currentMarker) {
    dom.currentMarker.textContent = state.currentMarkerFileName || "未选择"
  }
  if (dom.currentStem) {
    dom.currentStem.textContent = buildStem() || "-"
  }
}

function buildStem() {
  if (state.currentStemOverride) {
    return state.currentStemOverride
  }
  const audioStem = deriveStem(state.currentAudioFileName)
  const markerStem = deriveStem(state.currentMarkerFileName)
  return audioStem || markerStem || ""
}

function deriveStem(name) {
  if (!name) return ""
  const withoutExt = name.replace(/\.[^./]+$/, "")
  return withoutExt.replace(/\.audition_markers$/i, "").replace(/\.markers$/i, "")
}

function secondsToHms(value) {
  const millis = Math.max(0, Math.round(Number(value || 0) * 1000))
  const hours = Math.floor(millis / 3_600_000)
  const minutes = Math.floor((millis % 3_600_000) / 60_000)
  const seconds = Math.floor((millis % 60_000) / 1000)
  const ms = millis % 1000
  return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${seconds
    .toString()
    .padStart(2, "0")}.${ms.toString().padStart(3, "0")}`
}

function roundSeconds(value) {
  return Math.round(Number(value || 0) * 1000) / 1000
}

function formatSeconds(value) {
  return roundSeconds(value).toFixed(3)
}

function escapeCsvCell(cell) {
  const text = (cell ?? "").toString()
  if (/[",\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`
  }
  return text
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
    URL.revokeObjectURL(state.audioObjectUrl)
  }
})

init()
