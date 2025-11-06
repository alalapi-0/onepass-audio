// OnePass Audio Web 控制台主脚本：负责读取 out/ 产物、渲染波形并导出手工标记。

const WaveSurferLib = window.WaveSurfer  // 引入全局 WaveSurfer 库
const RegionsPlugin = WaveSurferLib?.Regions  // 读取区域插件，允许在波形上标注片段

const REGION_STATES = ["delete", "keep", "undecided"]  // 可用的区域状态顺序
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
const CSV_HEADER = ["Name", "Start", "Duration", "Type", "Description"]  // Audition CSV 表头

const QUERY_API_BASE = sanitizeBase(new URLSearchParams(window.location.search).get("api") || "")  // URL 中传入的 API 基址
let API_BASE = QUERY_API_BASE  // 当前使用的 API 地址
let resolvingApiPromise = null  // 处理并发探测时的 Promise 锁
const START_SERVICE_COMMAND = "python scripts/web_panel_server.py"  // 指引用户启动服务的命令

const state = {
  fileGroups: [],  // /api/list 返回的分组数据
  selectedAudio: null,  // 当前选择的音频条目
  selectedMarker: null,  // 当前选择的标记条目
  currentStem: null,  // 当前推导的 stem
  waveSurfer: null,  // WaveSurfer 实例
  regionsPlugin: null,  // 区域插件实例
  dragSelectionHandle: null,  // 记录正在拖动的区域句柄
  altPressed: false,  // 是否按下 Alt，控制拖拽创建区域
  allowProgrammaticRegion: false,  // 是否允许脚本主动创建区域（避免递归触发）
  skipDeletes: true,  // 播放时是否跳过删除状态
  selectedRegionId: null,  // 当前高亮区域的 ID
  skipGuardRegionId: null,  // 播放时的防跳转区域 ID
  apiAvailable: false,  // 当前 API 是否可用
  serviceToastShown: false,  // 是否已经提示过“启动服务”
  lastKnownApiBase: API_BASE || null,  // 最近一次成功的 API 地址
}

const dom = {
  fileGroups: document.getElementById("file-groups"),  // 侧边栏列表容器
  currentAudio: document.getElementById("current-audio"),  // 顶部栏音频显示
  currentMarker: document.getElementById("current-marker"),  // 顶部栏标记显示
  currentStem: document.getElementById("current-stem"),  // 顶部栏 stem 显示
  playToggle: document.getElementById("play-toggle"),  // 播放/暂停按钮
  seekBack: document.getElementById("seek-back"),  // 快退按钮
  seekForward: document.getElementById("seek-forward"),  // 快进按钮
  zoomRange: document.getElementById("zoom-range"),  // 缩放滑杆
  skipToggle: document.getElementById("skip-toggle"),  // 跳过删除段按钮
  regionTableBody: document.getElementById("region-tbody"),  // 区域表格主体
  exportEdl: document.getElementById("export-edl"),  // 导出 EDL 按钮
  exportMarkers: document.getElementById("export-markers"),  // 导出 Audition 标记按钮
  refreshButton: document.getElementById("refresh-button"),  // 重新扫描按钮
  apiStatusBadge: document.getElementById("api-status-badge"),  // 状态徽章
  parseError: document.getElementById("parse-error"),  // 解析错误提示
}

updateApiBadge("pending")  // 初始化徽章为“检测中”状态

if (!WaveSurferLib || !RegionsPlugin) {
  showToast("缺少 WaveSurfer 依赖，请确认 vendor/ 目录存在。", "error")
}

// 清理 API 基址字符串，移除尾部斜杠或空值。

function sanitizeBase(base) {
  if (!base || base === "null") return ""
  return base.replace(/\/+$, "")
}

// 根据查询参数、历史记录与默认端口生成可尝试的 API 地址列表。

function getCandidateBases() {
  const origin = window.location.origin && window.location.origin.startsWith("http") ? window.location.origin : ""
  const bases = [QUERY_API_BASE, API_BASE, origin, "http://127.0.0.1:8088"]
  const unique = []
  for (const item of bases) {
    const clean = sanitizeBase(item)
    if (clean && !unique.includes(clean)) {
      unique.push(clean)
    }
  }
  return unique
}

// 向指定地址发送 /api/ping 请求，用于探测服务是否在线。

async function pingBase(base) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 1000)
  try {
    const response = await fetch(`${base}/api/ping`, { cache: "no-store", signal: controller.signal })
    if (!response.ok) {
      return false
    }
    const text = await response.text()
    try {
      const payload = JSON.parse(text)
      return Boolean(payload?.ok)
    } catch (error) {
      console.warn("Ping JSON 解析失败", error)
      return false
    }
  } catch (error) {
    return false
  } finally {
    clearTimeout(timer)
  }
}

// 更新右上角状态徽章，展示在线/离线/检测中的状态。

function updateApiBadge(status, base) {
  if (!dom.apiStatusBadge) return
  const badge = dom.apiStatusBadge
  badge.classList.remove("status-badge--online", "status-badge--offline", "status-badge--pending")
  if (status === "online") {
    badge.classList.add("status-badge--online")
    const effective = base || API_BASE || state.lastKnownApiBase || ""
    badge.innerHTML = `API: ${effective}<span class="status-badge__check">✓</span>`
    state.apiAvailable = true
  } else if (status === "offline") {
    badge.classList.add("status-badge--offline")
    badge.textContent = "API 未运行（仅本地静态页面）"
    state.apiAvailable = false
  } else {
    badge.classList.add("status-badge--pending")
    badge.textContent = "API 状态检测中…"
  }
}

// 在状态徽章上标记当前服务不可用。

function markApiOffline() {
  updateApiBadge("offline")
}

// 弹出提示，告知尚未启动本地服务。

function showNoServiceToast() {
  const message =
    "未检测到本地服务。请在项目根运行：python scripts/web_panel_server.py（或在主菜单按 W）。" +
    "页面将继续运行，但无法读取 out/ 列表。"
  showToast(message, "warning")
}

// 弹出提示并附带复制按钮，指引用户启动后端服务。

function showStartServiceToast() {
  showToast(`请先启动服务：${START_SERVICE_COMMAND}`, "info", {
    copyText: START_SERVICE_COMMAND,
    copyLabel: "复制命令",
  })
}

// 在侧边栏显示占位文案，提醒列表不可用。

function showServiceUnavailableMessage() {
  if (dom.fileGroups) {
    dom.fileGroups.innerHTML = "<p class=\"sidebar__empty\">未连接到本地服务，无法读取 out/ 列表。</p>"
  }
}

// 依次尝试候选地址，找到可用的 API 基址并缓存。

async function resolveApiBase() {
  if (API_BASE && state.apiAvailable) {
    return API_BASE
  }
  if (resolvingApiPromise) {
    return resolvingApiPromise
  }
  const candidates = getCandidateBases()
  resolvingApiPromise = (async () => {
    for (const base of candidates) {
      if (!base) continue
      const ok = await pingBase(base)
      if (ok) {
        API_BASE = base
        state.lastKnownApiBase = base
        state.serviceToastShown = false
        updateApiBadge("online", base)
        return base
      }
    }
    markApiOffline()
    throw new Error("no-api")
  })()
  try {
    return await resolvingApiPromise
  } finally {
    resolvingApiPromise = null
  }
}

// 确保后端服务已就绪，必要时提示用户启动。

async function ensureApiReady(options = {}) {
  const { showToast = false } = options
  try {
    return await resolveApiBase()
  } catch (error) {
    markApiOffline()
    if (!state.serviceToastShown) {
      showNoServiceToast()
      state.serviceToastShown = true
    }
    if (showToast) {
      showStartServiceToast()
    }
    throw error
  }
}

// 将后端返回的单个分组整理为统一结构，方便前端使用。

function normalizeGroup(group) {
  if (!group) return null
  const stem = group.stem || ""
  const audio = Array.isArray(group.audio)
    ? group.audio.map((item) => normalizeFileInfo(item, "audio")).filter(Boolean)
    : []
  const markers = Array.isArray(group.markers)
    ? group.markers.map((item) => normalizeFileInfo(item, "marker")).filter(Boolean)
    : []
  audio.sort((a, b) => a.name.localeCompare(b.name, "zh-CN"))
  markers.sort((a, b) => a.name.localeCompare(b.name, "zh-CN"))
  return { stem, audio, markers }
}

// 将单个文件条目解析成统一的音频/标记对象。

function normalizeFileInfo(entry, type) {
  if (!entry) return null
  const result = { path: "", name: "", displayPath: "" }
  if (typeof entry === "string") {
    const trimmed = entry.replace(/^\.\/+/, "")
    const relative = trimmed.replace(/^out\//, "")
    result.path = relative
    result.name = trimmed.split("/").pop() || relative
    result.displayPath = relative ? `out/${relative}` : result.name
  } else if (typeof entry === "object") {
    const rawPath = typeof entry.path === "string" ? entry.path : ""
    const trimmed = rawPath.replace(/^\.\/+/, "")
    const relative = trimmed.replace(/^out\//, "")
    result.path = relative || rawPath
    result.name = entry.name || trimmed.split("/").pop() || result.path
    const explicitDisplay = entry.display_path || entry.displayPath
    result.displayPath = explicitDisplay || (relative ? `out/${relative}` : result.name)
    if (entry.kind) {
      result.kind = entry.kind
    }
  } else {
    return null
  }
  if (type === "marker" && !result.kind) {
    result.kind = inferMarkerKind(result.name)
  }
  return result
}

// 基于当前 API 基址拼接请求路径。

function buildApiUrl(path) {
  if (!path) return path
  if (/^https?:/i.test(path)) {
    return path
  }
  const base = sanitizeBase(API_BASE || state.lastKnownApiBase || "")
  if (path.startsWith("/")) {
    return base ? `${base}${path}` : path
  }
  return base ? `${base}/${path}` : path
}

// 封装 fetch 调用，处理 JSON 响应与错误提示。

async function fetchJSON(path, opts = {}) {
  const hasProtocol = /^https?:/i.test(path)
  let base = sanitizeBase(API_BASE || state.lastKnownApiBase || "")
  if (!hasProtocol && !base) {
    base = sanitizeBase(await resolveApiBase())
  }
  const url = hasProtocol ? path : `${base}${path}`
  const headers = { Accept: "application/json", ...(opts.headers || {}) }
  const fetchOptions = { ...opts, headers }
  let response
  try {
    response = await fetch(url, fetchOptions)
  } catch (error) {
    markApiOffline()
    throw error
  }
  const text = await response.text()
  const ct = response.headers.get("content-type") || ""
  if (!ct.includes("application/json")) {
    const snippet = text.slice(0, 120).replace(/\s+/g, " ").trim()
    const baseLabel = base || url
    throw new Error(`Non-JSON response (status ${response.status}) from ${baseLabel}: ${snippet}`)
  }
  try {
    const parsed = JSON.parse(text)
    if (!hasProtocol && base) {
      updateApiBadge("online", base)
      state.lastKnownApiBase = base
    }
    return parsed
  } catch (error) {
    const snippet = text.slice(0, 120).replace(/\s+/g, " ").trim()
    const baseLabel = base || url
    throw new Error(`Invalid JSON from ${baseLabel}: ${error.message}. Snippet: ${snippet}`)
  }
}

// 以纯文本形式请求资源，并处理网络错误。

async function fetchText(path, opts = {}) {
  const hasProtocol = /^https?:/i.test(path)
  let base = sanitizeBase(API_BASE || state.lastKnownApiBase || "")
  if (!hasProtocol && !base) {
    base = sanitizeBase(await resolveApiBase())
  }
  const url = hasProtocol ? path : `${base}${path}`
  let response
  try {
    response = await fetch(url, opts)
  } catch (error) {
    markApiOffline()
    throw error
  }
  const text = await response.text()
  if (!response.ok) {
    const ct = response.headers.get("content-type") || ""
    if (ct.includes("application/json")) {
      let message = `请求失败 (${response.status})`
      try {
        const payload = JSON.parse(text)
        if (payload && typeof payload.error === "string" && payload.error) {
          message = payload.error
        }
      } catch (parseError) {
        // ignore parse error and fallback to default message
      }
      throw new Error(message)
    }
    const snippet = text.slice(0, 120).replace(/\s+/g, " ").trim()
    throw new Error(`请求失败 (${response.status}): ${snippet}`)
  }
  if (!hasProtocol && base) {
    updateApiBadge("online", base)
    state.lastKnownApiBase = base
  }
  return text
}

// 调用 /api/list 接口获取 out/ 目录的分组信息。

async function fetchFileGroups(options = {}) {
  const { manual = false } = options
  try {
    await ensureApiReady({ showToast: manual })
  } catch (error) {
    if (!manual && !state.fileGroups.length) {
      showServiceUnavailableMessage()
    }
    return
  }
  try {
    const payload = await fetchJSON("/api/list", { cache: "no-store" })
    if (!payload.ok) {
      throw new Error(payload.error || "未知错误")
    }
    const groups = payload.groups || payload.items || []
    state.fileGroups = groups.map((group) => normalizeGroup(group)).filter(Boolean)
    renderFileGroups()
  } catch (error) {
    console.error("加载文件列表失败", error)
    showToast(`加载文件列表失败: ${error.message}`, "error")
  }
}

// 渲染左侧侧边栏，列出可选择的音频与标记文件。

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

// 从文件路径推导 stem 名称，用于匹配音频与标记。

function deriveStem(name) {
  if (!name) return ""
  const index = name.indexOf(".")
  return index === -1 ? name : name.slice(0, index)
}

// 在顶部栏刷新当前音频、标记与 stem 显示。

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

// 响应侧边栏点击事件，加载并播放目标音频。

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

// 选择标记文件并根据类型解析区域数据。

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

// 清空当前波形区域与表格状态。

function resetRegions() {
  state.selectedRegionId = null
  state.skipGuardRegionId = null
  if (state.regionsPlugin) {
    state.regionsPlugin.clearRegions()
  }
  dom.regionTableBody.innerHTML = ""
}

// 异步加载音频文件，初始化 WaveSurfer 实例。

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

// 销毁现有的 WaveSurfer，释放事件与内存。

function destroyWaveSurfer() {
  disableAltDrag()
  if (state.waveSurfer) {
    state.waveSurfer.destroy()
  }
  state.waveSurfer = null
  state.regionsPlugin = null
  resetRegions()
}

// 根据标记类型加载内容，渲染区域或生成提示。

async function loadMarker(marker) {
  if (!state.regionsPlugin) {
    resetRegions()
    return
  }
  clearParseError()
  if (!marker || !marker.path) {
    resetRegions()
    return
  }
  try {
    const text = await fetchText(`/api/file?path=${encodeURIComponent(marker.path)}`, { cache: "no-store" })
    const kind = marker.kind || inferMarkerKind(marker.name)
    let specs = []
    try {
      if (kind === "audition_csv" || kind === "markers_csv") {
        specs = parseAuditionCsv(text)
      } else if (kind === "edl_json") {
        specs = parseEdlJson(text)
      } else if (kind === "srt") {
        specs = parseSrt(text)
      } else {
        specs = parseAuditionCsv(text)
      }
    } catch (parseError) {
      reportParseFailure(marker, parseError, text)
      resetRegions()
      return
    }
    marker.kind = kind
    if (state.selectedMarker) {
      state.selectedMarker.kind = kind
    }
    applyRegions(specs)
    clearParseError()
    showToast(`已加载标记，共 ${specs.length} 段`, "success")
  } catch (error) {
    console.error("读取标记失败", error)
    resetRegions()
    showToast(`读取标记失败: ${error.message}`, "error")
  }
}

// 根据文件后缀判断标记格式（CSV/EDL/SRT）。

function inferMarkerKind(name) {
  const lower = name.toLowerCase()
  if (lower.endsWith(".edl.json")) return "edl_json"
  if (lower.endsWith(".srt")) return "srt"
  if (lower.endsWith(".audition_markers.csv")) return "audition_csv"
  if (lower.endsWith(".markers.csv")) return "markers_csv"
  return "unknown"
}

// 为 out/ 下的资源生成可访问的完整 URL。

function buildOutUrl(path) {
  const cleaned = (path || "").replace(/^\.\/+/g, "").replace(/^out\//, "")
  const encoded = cleaned
    .split("/")
    .filter((segment) => segment.length > 0)
    .map(encodeURIComponent)
    .join("/")
  return buildApiUrl(`/out/${encoded}`)
}

// 为波形区域注册交互事件，支持拖动与选中。

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

// 在创建或修改区域后，统一填充默认属性。

function finalizeRegion(region) {
  const stateKey = region.data.state || "undecided"
  region.setOptions({ color: REGION_COLORS[stateKey] })
  if (region.element) {
    region.element.dataset.state = stateKey
    region.element.classList.toggle("is-selected", region.id === state.selectedRegionId)
    region.element.title = buildRegionTooltip(region)
  }
}

// 为区域生成悬停提示文本。

function buildRegionTooltip(region) {
  const start = secondsToHms(region.start)
  const end = secondsToHms(region.end)
  const duration = secondsToHms(Math.max(0, region.end - region.start))
  const description = region.data.description ? `\n${region.data.description}` : ""
  return `${STATE_LABELS[region.data.state]} ${start} → ${end} (${duration})${description}`
}

// 根据解析结果批量创建 WaveSurfer 区域。

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

// 按时间排序当前区域列表，方便渲染与导出。

function getRegionsSorted() {
  if (!state.regionsPlugin) return []
  return [...state.regionsPlugin.getRegions()].sort((a, b) => a.start - b.start)
}

// 刷新下方表格，展示所有区域的时间与状态。

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

// 创建带默认类名的表格单元格元素。

function createCell(text) {
  const td = document.createElement("td")
  td.textContent = text
  return td
}

// 设置当前高亮的区域，并同步表格样式。

function setSelectedRegion(region) {
  state.selectedRegionId = region?.id || null
  for (const existing of getRegionsSorted()) {
    if (existing.element) {
      existing.element.classList.toggle("is-selected", existing.id === state.selectedRegionId)
    }
  }
  refreshRegionTable()
}

// 在删除/保留/未决之间循环切换区域状态。

function cycleRegionState(region) {
  const currentIndex = REGION_STATES.indexOf(region.data.state)
  const nextIndex = (currentIndex + 1) % REGION_STATES.length
  region.data.state = REGION_STATES[nextIndex]
  finalizeRegion(region)
  refreshRegionTable()
}

// 将某个状态批量应用到所有区域。

function applyStateToAll(nextState) {
  for (const region of getRegionsSorted()) {
    region.data.state = nextState
    finalizeRegion(region)
  }
  refreshRegionTable()
}

// 跟踪播放进度，使对应区域与表格行高亮。

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

// 切换音频的播放与暂停。

function togglePlayback() {
  if (!state.waveSurfer) return
  state.waveSurfer.playPause()
}

// 相对移动播放指针，实现快退/快进。

function seekBy(seconds) {
  if (!state.waveSurfer) return
  const current = state.waveSurfer.getCurrentTime?.() || 0
  let next = current + seconds
  next = Math.max(0, Math.min(next, state.waveSurfer.getDuration?.() || next))
  state.waveSurfer.setTime(next)
}

// 控制是否在播放时跳过删除状态的片段。

function toggleSkipDeletes() {
  state.skipDeletes = !state.skipDeletes
  dom.skipToggle.classList.toggle("active", state.skipDeletes)
  showToast(state.skipDeletes ? "播放时将跳过删除段" : "播放将包含删除段", "info")
}

// 根据滑杆数值调整波形缩放。

function handleZoomChange() {
  if (!state.waveSurfer) return
  const value = Number(dom.zoomRange.value) || 120
  state.waveSurfer.zoom(value)
}

// 记录 Alt 键按下状态，允许拖动创建区域。

function enableAltDrag() {
  if (!state.regionsPlugin || state.dragSelectionHandle) return
  state.dragSelectionHandle = state.regionsPlugin.enableDragSelection(
    { color: REGION_COLORS.delete, drag: true, resize: true },
    3,
  )
}

// Alt 键松开时还原拖动状态。

function disableAltDrag() {
  if (state.dragSelectionHandle) {
    state.dragSelectionHandle()
    state.dragSelectionHandle = null
  }
}

// 响应键盘按下事件，支持快捷键控制。

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

// 响应键盘松开事件，重置状态并更新界面。

function handleKeyUp(event) {
  if (event.key === "Alt") {
    state.altPressed = false
    disableAltDrag()
  }
}

// 在无需弹窗的情况下直接保存当前编辑结果。

async function quickSave() {
  const [edlOk, csvOk] = await Promise.all([exportEdl(true), exportMarkers(true)])
  if (edlOk && csvOk) {
    showToast("已保存 EDL 与 Audition CSV", "success")
  }
}

// 将区域转换为 EDL JSON 并通过 API 保存。

async function exportEdl(silent = false) {
  if (!state.currentStem) {
    if (!silent) showToast("尚未选择 stem，无法导出。", "error")
    return false
  }
  const actions = collectEdlActions()
  try {
    const payload = await fetchJSON("/api/save_edl", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stem: state.currentStem, actions }),
    })
    if (!payload.ok) {
      throw new Error(payload.error || "保存失败")
    }
    if (!silent) {
      showSavedFileToast(payload.path, "EDL")
    }
    return true
  } catch (error) {
    showToast(`导出 EDL 失败: ${error.message}`, "error")
    return false
  }
}

// 将区域导出为 Audition CSV 并上传保存。

async function exportMarkers(silent = false) {
  if (!state.currentStem) {
    if (!silent) showToast("尚未选择 stem，无法导出。", "error")
    return false
  }
  const rows = collectMarkersRows()
  try {
    const payload = await fetchJSON("/api/save_markers_csv", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stem: state.currentStem, rows }),
    })
    if (!payload.ok) {
      throw new Error(payload.error || "保存失败")
    }
    if (!silent) {
      showSavedFileToast(payload.path, "Audition CSV")
    }
    return true
  } catch (error) {
    showToast(`导出 CSV 失败: ${error.message}`, "error")
    return false
  }
}

// 从区域列表提取剪辑动作，供 EDL 使用。

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

// 构建 Audition CSV 所需的表格数据。

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

// 把秒数格式化为 HH:MM:SS.fff 字符串。

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

// 对秒数做四舍五入，避免浮点误差。

function roundSeconds(value) {
  return Math.round(Number(value || 0) * 1000) / 1000
}

// 在右上角显示临时提示，可选复制按钮。

function showToast(message, type = "info", options = {}) {
  const container = document.getElementById("toast-container")
  if (!container) return
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
  if (options.link && options.link.href) {
    const anchor = document.createElement("a")
    anchor.href = options.link.href
    anchor.className = "toast__link"
    if (options.link.newTab !== false) {
      anchor.target = "_blank"
      anchor.rel = "noopener"
    }
    anchor.textContent = options.link.text || options.link.href
    toast.appendChild(anchor)
  }
  if (options.copyText) {
    const actions = document.createElement("div")
    actions.className = "toast__actions"
    const button = document.createElement("button")
    button.type = "button"
    button.className = "toast__copy"
    const defaultLabel = options.copyLabel || "复制"
    button.textContent = defaultLabel
    button.addEventListener("click", async () => {
      try {
        await copyTextToClipboard(options.copyText)
        button.textContent = "已复制"
      } catch (error) {
        console.error("复制失败", error)
        button.textContent = "复制失败"
      }
      setTimeout(() => {
        button.textContent = defaultLabel
      }, 1500)
    })
    actions.appendChild(button)
    toast.appendChild(actions)
  }
  container.appendChild(toast)
  setTimeout(() => {
    toast.style.opacity = "0"
    toast.style.transform = "translateY(-10px)"
    setTimeout(() => toast.remove(), 300)
  }, options.duration || 4000)
}

// 复制文本到剪贴板，并在失败时告警。

async function copyTextToClipboard(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  const textarea = document.createElement("textarea")
  textarea.value = text
  textarea.style.position = "fixed"
  textarea.style.top = "-1000px"
  document.body.appendChild(textarea)
  textarea.focus()
  textarea.select()
  document.execCommand("copy")
  document.body.removeChild(textarea)
}

// 保存成功后弹出可点击的提示。

function showSavedFileToast(path, label) {
  const normalized = (path || "").replace(/^\.\/+/, "")
  const prefixed = normalized.startsWith("out/") || !normalized ? normalized : `out/${normalized}`
  const relative = prefixed.replace(/^out\//, "")
  const href = buildApiUrl(`/api/file?path=${encodeURIComponent(relative)}`)
  const text = prefixed || path || "out/"
  const message = label ? `已保存 ${label} → ` : "已保存 → "
  showToast(message, "success", { link: { href, text } })
}

// 在顶部显示解析错误提示框。

function showParseError(message) {
  if (!dom.parseError) return
  dom.parseError.textContent = message
  dom.parseError.classList.remove("hidden")
}

// 隐藏解析错误提示，恢复正常界面。

function clearParseError() {
  if (!dom.parseError) return
  dom.parseError.textContent = ""
  dom.parseError.classList.add("hidden")
}

// 记录标记文件解析失败的详细原因。

function reportParseFailure(marker, error, text) {
  const name = marker?.name || "标记"
  console.error(`解析 ${name} 失败`, error)
  if (typeof text === "string" && text.length) {
    console.error("原始文本片段:", text.slice(0, 200))
  }
  showParseError(`解析 ${name} 失败：${error.message}`)
}

// 解析 Audition 标记 CSV，返回区域与错误信息。

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

// 解析单行 CSV，处理引号与分隔符。

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

// 解析 EDL JSON 文件，转换为区域数组。

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

// 解析 SRT 字幕，提取时间轴与文本。

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

// 将多种时间表示转换为秒。

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

// 绑定按钮、滑杆与键盘的交互事件。

function attachEventListeners() {
  dom.playToggle.addEventListener("click", togglePlayback)
  dom.seekBack.addEventListener("click", () => seekBy(-5))
  dom.seekForward.addEventListener("click", () => seekBy(5))
  dom.skipToggle.addEventListener("click", toggleSkipDeletes)
  dom.zoomRange.addEventListener("input", handleZoomChange)
  dom.exportEdl.addEventListener("click", () => exportEdl(false))
  dom.exportMarkers.addEventListener("click", () => exportMarkers(false))
  dom.refreshButton.addEventListener("click", () => fetchFileGroups({ manual: true }))
  document.querySelectorAll("[data-bulk]").forEach((button) => {
    button.addEventListener("click", () => applyStateToAll(button.dataset.bulk))
  })
  window.addEventListener("keydown", handleKeyDown)
  window.addEventListener("keyup", handleKeyUp)
}

// 初始化应用，加载文件列表并准备界面。

function init() {
  attachEventListeners()
  updateSelectionInfo()
  clearParseError()
  fetchFileGroups()
}

init()
