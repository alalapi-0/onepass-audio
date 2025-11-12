const tableBody = () => document.querySelector("#segments-table tbody");

export function initUI({
  onRefresh,
  onStemSelected,
  onPlay,
  onImportCsv,
  onImportAudio,
  onSave,
  onToggleSync,
}) {
  document.querySelector("#refresh-stems").addEventListener("click", onRefresh);
  document.querySelector("#sync-toggle").addEventListener("change", (event) => {
    onToggleSync(event.target.checked);
  });
  document.querySelectorAll(".player-play").forEach((button) => {
    button.addEventListener("click", () => onPlay(button.dataset.player));
  });
  document.querySelector("#import-csv").addEventListener("click", onImportCsv);
  document.querySelector("#import-audio").addEventListener("click", onImportAudio);
  document.querySelector("#save-all").addEventListener("click", onSave);

  document.addEventListener("click", (event) => {
    const item = event.target.closest(".sidebar__item");
    if (item && item.dataset.stem) {
      onStemSelected(item.dataset.stem);
    }
  });
}

export function renderStemsList(stems, currentStem) {
  const list = document.querySelector("#stem-list");
  list.innerHTML = "";
  stems.forEach((stem) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sidebar__item";
    if (stem.stem === currentStem) {
      button.classList.add("is-active");
    }
    button.dataset.stem = stem.stem;
    button.innerHTML = `
      <div class="stem-name">${stem.stem}</div>
      <div class="stem-meta">${stem.clean_audio ? "已剪辑" : "未剪辑"}</div>
    `;
    list.appendChild(button);
  });
}

export function updateStemHeader({ stem, source, rendered }) {
  const title = document.querySelector("#stem-title");
  const summary = document.querySelector("#stem-summary");
  if (!stem) {
    title.textContent = "请选择要处理的 stem";
    summary.textContent = "";
    return;
  }
  title.textContent = stem;
  const parts = [];
  parts.push(source ? "源音频已就绪" : "缺少源音频，可导入");
  parts.push(rendered ? "剪辑音频已就绪" : "缺少干净音频，可导入");
  summary.textContent = parts.join(" · ");
}

export function updatePlayerMeta(type, text) {
  const el = document.querySelector(`#${type}-meta`);
  if (el) {
    el.textContent = text;
  }
}

export function updatePlayerStatus(type, text) {
  const el = document.querySelector(`.player__status[data-player="${type}"]`);
  if (el) {
    el.textContent = text;
  }
}

export function renderSegmentsTable(segments, selectedId, handlers) {
  const tbody = tableBody();
  tbody.innerHTML = "";
  segments.forEach((segment, index) => {
    const row = document.createElement("tr");
    row.dataset.segmentId = segment.id;
    if (segment.id === selectedId) {
      row.classList.add("is-selected");
    }
    const duration = Math.max(segment.end - segment.start, 0);
    row.innerHTML = `
      <td>${index + 1}</td>
      <td><input type="text" name="name" value="${escapeHtml(segment.name || "")}" /></td>
      <td>${segment.start.toFixed(3)}</td>
      <td>${segment.end.toFixed(3)}</td>
      <td>${duration.toFixed(3)}</td>
      <td>
        <select name="label">
          ${["keep", "delete", "undecided"].map((label) => `<option value="${label}" ${segment.label === label ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </td>
      <td><textarea name="comment" rows="1">${escapeHtml(segment.comment || "")}</textarea></td>
    `;
    row.addEventListener("click", (event) => {
      if (event.target.closest("input, textarea, select")) {
        return;
      }
      handlers.onSelect(segment.id);
    });
    row.querySelector('input[name="name"]').addEventListener("input", (event) => {
      handlers.onFieldChange(segment.id, "name", event.target.value);
    });
    row.querySelector('textarea[name="comment"]').addEventListener("input", (event) => {
      event.target.style.height = "auto";
      event.target.style.height = `${event.target.scrollHeight}px`;
      handlers.onFieldChange(segment.id, "comment", event.target.value);
    });
    row.querySelector('select[name="label"]').addEventListener("change", (event) => {
      handlers.onFieldChange(segment.id, "label", event.target.value);
    });
    tbody.appendChild(row);
  });
}

export function showToast(message, { title = "提示", variant = "info", duration = 3200 } = {}) {
  const container = document.querySelector("#toast-container");
  const toast = document.createElement("div");
  toast.className = "toast";
  if (variant === "error") {
    toast.classList.add("is-error");
  }
  toast.innerHTML = `
    <div class="toast__title">${escapeHtml(title)}</div>
    <div class="toast__message">${escapeHtml(message)}</div>
  `;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add("is-leaving");
    toast.style.opacity = "0";
    setTimeout(() => {
      toast.remove();
    }, 400);
  }, duration);
}

export function toggleDropMask(show) {
  const mask = document.querySelector("#drop-mask");
  mask.hidden = !show;
}

export function openFileDialog({ accept, multiple = false }, callback) {
  const input = document.querySelector("#hidden-file");
  input.accept = accept;
  input.multiple = multiple;
  input.value = "";
  input.onchange = (event) => {
    const files = Array.from(event.target.files || []);
    callback(files);
    input.value = "";
  };
  input.click();
}

export function highlightSegment(id) {
  const tbody = tableBody();
  tbody.querySelectorAll("tr").forEach((row) => {
    row.classList.toggle("is-selected", row.dataset.segmentId === id);
  });
}

export function bindGlobalShortcuts(handler) {
  document.addEventListener("keydown", handler, true);
}

export function updateSyncToggle(value) {
  const toggle = document.querySelector("#sync-toggle");
  toggle.checked = !!value;
}

function escapeHtml(text) {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function promptAudioType() {
  return new Promise((resolve, reject) => {
    const wrapper = document.createElement("div");
    wrapper.className = "toast";
    wrapper.innerHTML = `
      <div class="toast__title">选择导入类型</div>
      <div class="toast__message">将音频替换为源音频还是干净音频?</div>
      <div style="display:flex; gap:0.5rem; margin-top:0.6rem;">
        <button type="button" data-value="audio_source">替换源音频</button>
        <button type="button" data-value="audio_clean">替换干净音频</button>
        <button type="button" data-action="cancel">取消</button>
      </div>
    `;
    const container = document.querySelector("#toast-container");
    container.appendChild(wrapper);
    const cleanup = () => {
      wrapper.remove();
    };
    wrapper.querySelectorAll("button").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.action;
        if (action === "cancel") {
          cleanup();
          reject(new Error("已取消"));
          return;
        }
        const value = button.dataset.value;
        cleanup();
        resolve(value);
      });
    });
  });
}

export function showError(message) {
  showToast(message, { variant: "error", title: "错误" });
}

