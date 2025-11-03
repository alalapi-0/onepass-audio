const WaveSurferLib = window.WaveSurfer
const RegionsPlugin = WaveSurferLib?.Regions

const REGION_STATES = ["delete", "keep", "undecided"]
const REGION_COLORS = {
  delete: "rgba(239,68,68,0.35)",
  keep: "rgba(34,197,94,0.35)",
  undecided: "rgba(148,163,184,0.4)",
}
const STATE_LABELS = {
  delete: "删除",
  keep: "保留",
  undecided: "未决",
}
const CSV_HEADER = ["Name", "Start", "Duration", "Type", "Description"]

const state = {
  fileGroups: [],
  selectedAudio: null,
  selectedMarker: null,
  currentStem: null,
  waveSurfer: null,
  regionsPlugin: null,
  dragSelectionHandle: null,
  altPressed: false,
  allowProgrammaticRegion: false,
  skipDeletes: true,
  selectedRegionId: null,
  skipGuardRegionId: null,
}

const dom = {
  fileGroups: document.getElementById("file-groups"),
  currentAudio: document.getElementById("current-audio"),
  currentMarker: document.getElementById("current-marker"),
  currentStem: document.getElementById("current-stem"),
  playToggle: document.getElementById("play-toggle"),
  seekBack: document.getElementById("seek-back"),
  seekForward: document.getElementById("seek-forward"),
  zoomRange: document.getElementById("zoom-range"),
  skipToggle: document.getElementById("skip-toggle"),
  regionTableBody: document.getElementById("region-tbody"),
  exportEdl: document.getElementById("export-edl"),
  exportMarkers: document.getElementById("export-markers"),
  refreshButton: document.getElementById("refresh-button"),
}

if (!WaveSurferLib || !RegionsPlugin) {
  showToast("缺少 WaveSurfer 依赖，请确认 vendor/ 目录存在。", "error")
}

async function fetchFileGroups() {
  try {
    const response = await fetch("/api/list", { cache: "no-store" })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.error || "未知错误")
    }
    state.fileGroups = payload.items || []
    renderFileGroups()
  } catch (error) {
    console.error("加载文件列表失败", error)
    showToast(`加载文件列表失败: ${error.message}`, "error")
  }
}

function renderFileGroups() {
  dom.fileGroups.innerHTML = ""
  if (!state.fileGroups.length) {
    dom.fileGroups.innerHTML = "<p>out/ 目录下未找到音频或标记文件。</p>"
    return
  }
  for (const group of state.fileGroups) {
    const wrapper = document.createElement("div")
    wrapper.className = "stem-group"

    const title = document.createElement("h2")
    title.className = "stem-group__title"
    title.textContent = group.stem
    wrapper.appendChild(title)

    const audioSection = document.createElement("div")
    audioSection.className = "stem-group__section"
    const audioTitle = document.createElement("div")
    audioTitle.className = "stem-group__section-title"
    audioTitle.textContent = "音频"
    audioSection.appendChild(audioTitle)
    const audioContainer = document.createElement("div")
    audioContainer.className = "stem-group__files"
    for (const audio of group.audio) {
      const pill = document.createElement("button")
      pill.type = "button"
      pill.className = "file-pill"
      pill.textContent = audio.name
      if (state.selectedAudio && state.selectedAudio.path === audio.path) {
        pill.classList.add("is-selected")
      }
      pill.addEventListener("click", () => selectAudio(audio))
      audioContainer.appendChild(pill)
    }
    if (!group.audio.length) {
      const empty = document.createElement("span")
      empty.className = "sidebar__empty"
      empty.textContent = "无音频"
      audioContainer.appendChild(empty)
    }
    audioSection.appendChild(audioContainer)
    wrapper.appendChild(audioSection)

    const markerSection = document.createElement("div")
    markerSection.className = "stem-group__section"
    const markerTitle = document.createElement("div")
    markerTitle.className = "stem-group__section-title"
    markerTitle.textContent = "标记"
    markerSection.appendChild(markerTitle)
    const markerContainer = document.createElement("div")
    markerContainer.className = "stem-group__files"
    for (const marker of group.markers) {
      const pill = document.createElement("button")
      pill.type = "button"
      pill.className = "file-pill"
      pill.textContent = marker.name
      if (state.selectedMarker && state.selectedMarker.path === marker.path) {
        pill.classList.add("is-selected")
      }
      pill.addEventListener("click", () => selectMarker(marker))
      markerContainer.appendChild(pill)
    }
    if (!group.markers.length) {
      const empty = document.createElement("span")
      empty.className = "sidebar__empty"
      empty.textContent = "无标记"
      markerContainer.appendChild(empty)
    }
    markerSection.appendChild(markerContainer)
    wrapper.appendChild(markerSection)

    dom.fileGroups.appendChild(wrapper)
  }
}

function deriveStem(name) {
  if (!name) return ""
  const index = name.indexOf(".")
  return index === -1 ? name : name.slice(0, index)
}

function updateSelectionInfo() {
  dom.currentAudio.textContent = state.selectedAudio?.name || "未选择"
  dom.currentMarker.textContent = state.selectedMarker?.name || "未选择"
  let stem = state.currentStem
  if (!stem) {
    stem = deriveStem(state.selectedAudio?.name || state.selectedMarker?.name || "")
  }
  state.currentStem = stem || null
  dom.currentStem.textContent = state.currentStem || "-"
}

function selectAudio(audio) {
  if (state.selectedAudio && state.selectedAudio.path === audio.path) {
    return
  }
  state.selectedAudio = audio
  state.currentStem = deriveStem(audio.name)
  updateSelectionInfo()
  renderFileGroups()
  loadAudio(audio)
  if (state.selectedMarker) {
    const markerStem = deriveStem(state.selectedMarker.name)
    if (markerStem && markerStem !== state.currentStem) {
      showToast(`音频与标记 stem 不一致: ${state.currentStem} vs ${markerStem}`, "error")
    }
  }
}

function selectMarker(marker) {
  if (state.selectedMarker && state.selectedMarker.path === marker.path) {
    return
  }
  state.selectedMarker = marker
  if (!state.currentStem) {
    state.currentStem = deriveStem(marker.name)
  }
  updateSelectionInfo()
  renderFileGroups()
  loadMarker(marker)
  if (state.selectedAudio) {
    const audioStem = deriveStem(state.selectedAudio.name)
    if (audioStem && audioStem !== state.currentStem) {
      showToast(`标记与音频 stem 不一致: ${state.currentStem} vs ${audioStem}`, "error")
    }
  }
}

function resetRegions() {
  state.selectedRegionId = null
  state.skipGuardRegionId = null
  if (state.regionsPlugin) {
    state.regionsPlugin.clearRegions()
  }
  dom.regionTableBody.innerHTML = ""
}

async function loadAudio(audio) {
  destroyWaveSurfer()
  updateSelectionInfo()
  const url = buildOutUrl(audio.path)
  const minPxPerSec = Number(dom.zoomRange.value) || 120
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
  waveSurfer.load(url)
  waveSurfer.on("ready", () => {
    waveSurfer.zoom(minPxPerSec)
    if (state.selectedMarker) {
      loadMarker(state.selectedMarker)
    }
  })
  waveSurfer.on("timeupdate", handleTimeUpdate)
  waveSurfer.on("finish", () => {
    state.skipGuardRegionId = null
  })
}

function destroyWaveSurfer() {
  disableAltDrag()
  if (state.waveSurfer) {
    state.waveSurfer.destroy()
  }
  state.waveSurfer = null
  state.regionsPlugin = null
  resetRegions()
}

async function loadMarker(marker) {
  if (!state.regionsPlugin) {
    resetRegions()
    return
  }
  try {
    const response = await fetch(`/api/file?path=${encodeURIComponent(marker.path)}`)
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.error || "读取失败")
    }
    const text = payload.content || ""
    const kind = marker.kind || inferMarkerKind(marker.name)
    let specs = []
    if (kind === "audition_csv" || kind === "markers_csv") {
      specs = parseAuditionCsv(text)
    } else if (kind === "edl_json") {
      specs = parseEdlJson(text)
    } else if (kind === "srt") {
      specs = parseSrt(text)
    } else {
      specs = parseAuditionCsv(text)
    }
    applyRegions(specs)
    showToast(`已加载标记，共 ${specs.length} 段`, "success")
  } catch (error) {
    console.error("解析标记失败", error)
    resetRegions()
    showToast(`解析标记失败: ${error.message}`, "error")
  }
}

function inferMarkerKind(name) {
  const lower = name.toLowerCase()
  if (lower.endsWith(".edl.json")) return "edl_json"
  if (lower.endsWith(".srt")) return "srt"
  if (lower.endsWith(".audition_markers.csv")) return "audition_csv"
  if (lower.endsWith(".markers.csv")) return "markers_csv"
  return "unknown"
}

function buildOutUrl(path) {
  return `/out/${path.split("/").map(encodeURIComponent).join("/")}`
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
    region.data.state = region.data.state || (state.altPressed ? "delete" : "undecided")
    region.data.description = region.data.description || ""
    region.data.label = region.data.label || region.id
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
  const stateKey = region.data.state || "undecided"
  region.setOptions({ color: REGION_COLORS[stateKey] })
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

function applyRegions(specs) {
  resetRegions()
  if (!state.regionsPlugin) return
  const sorted = [...specs].sort((a, b) => a.start - b.start)
  for (const spec of sorted) {
    const start = Math.max(0, spec.start)
    const end = Math.max(start, spec.end)
    state.allowProgrammaticRegion = true
    const region = state.regionsPlugin.addRegion({
      start,
      end,
      drag: true,
      resize: true,
      data: {
        state: spec.state || "undecided",
        description: spec.description || "",
        label: spec.label || "",
      },
    })
    finalizeRegion(region)
  }
  refreshRegionTable()
}

function getRegionsSorted() {
  if (!state.regionsPlugin) return []
  return [...state.regionsPlugin.getRegions()].sort((a, b) => a.start - b.start)
}

function refreshRegionTable() {
  const regions = getRegionsSorted()
  dom.regionTableBody.innerHTML = ""
  regions.forEach((region, index) => {
    const row = document.createElement("tr")
    row.dataset.regionId = region.id
    if (region.id === state.selectedRegionId) {
      row.classList.add("is-selected")
    }
    const stateCell = document.createElement("td")
    const pill = document.createElement("span")
    pill.className = `region-pill ${region.data.state}`
    pill.textContent = STATE_LABELS[region.data.state]
    stateCell.appendChild(pill)

    row.appendChild(createCell(String(index + 1)))
    row.appendChild(stateCell)
    row.appendChild(createCell(secondsToHms(region.start)))
    row.appendChild(createCell(secondsToHms(region.end)))
    row.appendChild(createCell(secondsToHms(Math.max(0, region.end - region.start))))
    row.appendChild(createCell(region.data.description || region.data.label || ""))

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
  for (const existing of getRegionsSorted()) {
    if (existing.element) {
      existing.element.classList.toggle("is-selected", existing.id === state.selectedRegionId)
    }
  }
  refreshRegionTable()
}

function cycleRegionState(region) {
  const currentIndex = REGION_STATES.indexOf(region.data.state)
  const nextIndex = (currentIndex + 1) % REGION_STATES.length
  region.data.state = REGION_STATES[nextIndex]
  finalizeRegion(region)
  refreshRegionTable()
}

function applyStateToAll(nextState) {
  for (const region of getRegionsSorted()) {
    region.data.state = nextState
    finalizeRegion(region)
  }
  refreshRegionTable()
}

function handleTimeUpdate(time) {
  if (!state.skipDeletes || !state.waveSurfer) return
  const deleteRegion = getRegionsSorted().find(
    (region) => region.data.state === "delete" && time >= region.start && time < region.end - 0.01,
  )
  if (!deleteRegion) {
    state.skipGuardRegionId = null
    return
  }
  if (state.skipGuardRegionId === deleteRegion.id) {
    return
  }
  state.skipGuardRegionId = deleteRegion.id
  state.waveSurfer.setTime(deleteRegion.end + 0.01)
}

function togglePlayback() {
  if (!state.waveSurfer) return
  state.waveSurfer.playPause()
}

function seekBy(seconds) {
  if (!state.waveSurfer) return
  const current = state.waveSurfer.getCurrentTime?.() || 0
  let next = current + seconds
  next = Math.max(0, Math.min(next, state.waveSurfer.getDuration?.() || next))
  state.waveSurfer.setTime(next)
}

function toggleSkipDeletes() {
  state.skipDeletes = !state.skipDeletes
  dom.skipToggle.classList.toggle("active", state.skipDeletes)
  showToast(state.skipDeletes ? "播放时将跳过删除段" : "播放将包含删除段", "info")
}

function handleZoomChange() {
  if (!state.waveSurfer) return
  const value = Number(dom.zoomRange.value) || 120
  state.waveSurfer.zoom(value)
}

function enableAltDrag() {
  if (!state.regionsPlugin || state.dragSelectionHandle) return
  state.dragSelectionHandle = state.regionsPlugin.enableDragSelection(
    { color: REGION_COLORS.delete, drag: true, resize: true },
    3,
  )
}

function disableAltDrag() {
  if (state.dragSelectionHandle) {
    state.dragSelectionHandle()
    state.dragSelectionHandle = null
  }
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
    const region = getRegionsSorted().find((r) => r.id === state.selectedRegionId)
    if (region) {
      cycleRegionState(region)
    }
  } else if (event.key === "Delete") {
    event.preventDefault()
    const region = getRegionsSorted().find((r) => r.id === state.selectedRegionId)
    if (region) {
      region.remove()
      state.selectedRegionId = null
    }
  } else if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
    event.preventDefault()
    const regions = getRegionsSorted()
    if (!regions.length) return
    const currentIndex = regions.findIndex((region) => region.id === state.selectedRegionId)
    let nextIndex = currentIndex
    if (event.key === "ArrowRight") {
      nextIndex = currentIndex >= 0 ? Math.min(currentIndex + 1, regions.length - 1) : 0
    } else {
      nextIndex = currentIndex > 0 ? currentIndex - 1 : 0
    }
    setSelectedRegion(regions[nextIndex])
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
    event.preventDefault()
    quickSave()
  }
}

function handleKeyUp(event) {
  if (event.key === "Alt") {
    state.altPressed = false
    disableAltDrag()
  }
}

async function quickSave() {
  const [edlOk, csvOk] = await Promise.all([exportEdl(true), exportMarkers(true)])
  if (edlOk && csvOk) {
    showToast("已保存 EDL 与 Audition CSV", "success")
  }
}

async function exportEdl(silent = false) {
  if (!state.currentStem) {
    if (!silent) showToast("尚未选择 stem，无法导出。", "error")
    return false
  }
  const actions = collectEdlActions()
  try {
    const response = await fetch("/api/save_edl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stem: state.currentStem, actions }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.error || "保存失败")
    }
    if (!silent) {
      showToastWithLink(`已导出 EDL`, payload.path, "success")
    }
    return true
  } catch (error) {
    showToast(`导出 EDL 失败: ${error.message}`, "error")
    return false
  }
}

async function exportMarkers(silent = false) {
  if (!state.currentStem) {
    if (!silent) showToast("尚未选择 stem，无法导出。", "error")
    return false
  }
  const rows = collectMarkersRows()
  try {
    const response = await fetch("/api/save_markers_csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stem: state.currentStem, rows }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      throw new Error(payload.error || "保存失败")
    }
    if (!silent) {
      showToastWithLink(`已导出 Audition CSV`, payload.path, "success")
    }
    return true
  } catch (error) {
    showToast(`导出 CSV 失败: ${error.message}`, "error")
    return false
  }
}

function collectEdlActions() {
  const actions = []
  for (const region of getRegionsSorted()) {
    if (region.data.state !== "delete") continue
    const start = roundSeconds(region.start)
    const end = roundSeconds(region.end)
    if (end <= start) continue
    actions.push({ type: "cut", start, end, reason: region.data.reason || "manual" })
  }
  return actions
}

function collectMarkersRows() {
  const rows = [CSV_HEADER]
  let index = 1
  for (const region of getRegionsSorted()) {
    const start = secondsToHms(region.start)
    const end = secondsToHms(region.end)
    const duration = secondsToHms(Math.max(0, region.end - region.start))
    const suffix = String(index).padStart(3, "0")
    const description = region.data.description || region.data.label || ""
    rows.push([`CUT_${suffix}`, start, "00:00:00.000", "Marker", `[${STATE_LABELS[region.data.state]}] ${description}`.trim()])
    rows.push([`END_${suffix}`, end, "00:00:00.000", "Marker", `[${STATE_LABELS[region.data.state]}] ${description}`.trim()])
    rows.push([`CUTSPAN_${suffix}`, start, duration, "Marker", `[${STATE_LABELS[region.data.state]}] ${description}`.trim()])
    index += 1
  }
  return rows
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

function showToast(message, type = "info") {
  showToastWithLink(message, null, type)
}

function showToastWithLink(message, link, type = "info") {
  const container = document.getElementById("toast-container")
  const toast = document.createElement("div")
  toast.className = "toast"
  if (type === "success") toast.classList.add("toast--success")
  if (type === "error") toast.classList.add("toast--error")
  toast.textContent = message
  if (link) {
    const anchor = document.createElement("a")
    anchor.href = `/${link}`
    anchor.target = "_blank"
    anchor.rel = "noopener"
    anchor.className = "toast__link"
    anchor.textContent = "打开"
    toast.appendChild(anchor)
  }
  container.appendChild(toast)
  setTimeout(() => {
    toast.style.opacity = "0"
    toast.style.transform = "translateY(-10px)"
    setTimeout(() => toast.remove(), 300)
  }, 4000)
}

function parseAuditionCsv(text) {
  const lines = text.replace(/^\ufeff/, "").split(/\r?\n/)
  const filtered = lines.filter((line) => line.trim().length > 0)
  if (!filtered.length) return []
  const delimiter = filtered[0].includes(";") ? ";" : ","
  const rows = filtered.map((line) => parseCsvLine(line, delimiter))
  const header = rows.shift() || []
  const normalizedHeader = header.map((cell) => cell.trim().toLowerCase())
  const hasHeader = CSV_HEADER.every((key) => normalizedHeader.includes(key.toLowerCase()))
  const body = hasHeader ? rows : [header, ...rows]
  const cutStarts = new Map()
  const regions = []
  for (const row of body) {
    if (row.length < 3) continue
    const name = row[0].trim()
    const start = parseTime(row[1])
    const duration = parseTime(row[2])
    const description = row[4] || ""
    if (Number.isNaN(start)) continue
    if (name.startsWith("CUTSPAN_")) {
      const end = !Number.isNaN(duration) ? start + duration : start
      regions.push({
        start,
        end,
        state: "delete",
        description,
        label: name,
      })
    } else if (name.startsWith("CUT_")) {
      cutStarts.set(name.replace("CUT_", ""), start)
    } else if (name.startsWith("END_")) {
      const key = name.replace("END_", "")
      if (cutStarts.has(key)) {
        regions.push({
          start: cutStarts.get(key),
          end: start,
          state: "delete",
          description,
          label: `CUTSPAN_${key}`,
        })
      }
    } else {
      const end = !Number.isNaN(duration) ? start + duration : start
      const state = /^L\d+/i.test(name) ? "keep" : "undecided"
      regions.push({
        start,
        end,
        state,
        description,
        label: name,
      })
    }
  }
  return regions
}

function parseCsvLine(line, delimiter) {
  const result = []
  let current = ""
  let inQuotes = false
  for (let i = 0; i < line.length; i += 1) {
    const char = line[i]
    if (char === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"'
        i += 1
      } else {
        inQuotes = !inQuotes
      }
    } else if (char === delimiter && !inQuotes) {
      result.push(current.trim())
      current = ""
    } else {
      current += char
    }
  }
  result.push(current.trim())
  return result
}

function parseEdlJson(text) {
  const regions = []
  try {
    const payload = JSON.parse(text)
    const actions = Array.isArray(payload.actions) ? payload.actions : []
    for (const action of actions) {
      if (!action || action.type !== "cut") continue
      const start = Number(action.start)
      const end = Number(action.end)
      if (Number.isNaN(start) || Number.isNaN(end) || end <= start) continue
      regions.push({
        start,
        end,
        state: "delete",
        description: action.reason || "manual",
        label: "EDL",
      })
    }
  } catch (error) {
    console.error("解析 EDL JSON 失败", error)
    throw error
  }
  return regions
}

function parseSrt(text) {
  const normalized = text.replace(/^\ufeff/, "").replace(/\r\n/g, "\n")
  const blocks = normalized.split(/\n\n+/)
  const regions = []
  for (const block of blocks) {
    const lines = block.trim().split(/\n/)
    if (lines.length < 2) continue
    const timeLineIndex = lines.findIndex((line) => line.includes("--"))
    if (timeLineIndex === -1) continue
    const timeLine = lines[timeLineIndex]
    const [startText, endText] = timeLine.split("-->")
    if (!endText) continue
    const start = parseTime(startText)
    const end = parseTime(endText)
    if (Number.isNaN(start) || Number.isNaN(end) || end <= start) continue
    const textContent = lines.slice(timeLineIndex + 1).join("\n")
    regions.push({
      start,
      end,
      state: "undecided",
      description: textContent,
      label: `SRT ${regions.length + 1}`,
    })
  }
  return regions
}

function parseTime(value) {
  if (typeof value === "number") return value
  if (!value) return NaN
  const cleaned = value.toString().trim()
  if (!cleaned) return NaN
  if (cleaned.includes(":")) {
    const replaced = cleaned.replace(/,/g, ".")
    const parts = replaced.split(":")
    if (parts.length === 3) {
      const [h, m, s] = parts
      const seconds = parseFloat(s)
      if ([h, m, seconds].some((num) => Number.isNaN(Number(num)))) return NaN
      return Number(h) * 3600 + Number(m) * 60 + Number(seconds)
    }
  }
  const parsed = parseFloat(cleaned)
  return Number.isNaN(parsed) ? NaN : parsed
}

function attachEventListeners() {
  dom.playToggle.addEventListener("click", togglePlayback)
  dom.seekBack.addEventListener("click", () => seekBy(-5))
  dom.seekForward.addEventListener("click", () => seekBy(5))
  dom.skipToggle.addEventListener("click", toggleSkipDeletes)
  dom.zoomRange.addEventListener("input", handleZoomChange)
  dom.exportEdl.addEventListener("click", () => exportEdl(false))
  dom.exportMarkers.addEventListener("click", () => exportMarkers(false))
  dom.refreshButton.addEventListener("click", fetchFileGroups)
  document.querySelectorAll("[data-bulk]").forEach((button) => {
    button.addEventListener("click", () => applyStateToAll(button.dataset.bulk))
  })
  window.addEventListener("keydown", handleKeyDown)
  window.addEventListener("keyup", handleKeyUp)
}

function init() {
  attachEventListeners()
  updateSelectionInfo()
  fetchFileGroups()
}

init()
