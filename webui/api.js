const JSON_HEADERS = {
  Accept: "application/json",
};

async function request(url, options = {}) {
  const finalOptions = {
    credentials: "same-origin",
    ...options,
  };
  if (finalOptions.body && !(finalOptions.body instanceof FormData)) {
    const headers = {
      ...JSON_HEADERS,
      ...(finalOptions.headers || {}),
    };
    if (!("Content-Type" in headers)) {
      headers["Content-Type"] = "application/json";
    }
    finalOptions.headers = headers;
    if (typeof finalOptions.body !== "string") {
      finalOptions.body = JSON.stringify(finalOptions.body);
    }
  }
  const response = await fetch(url, finalOptions);
  const contentType = response.headers.get("content-type") || "";
  let data = null;
  if (contentType.includes("application/json")) {
    data = await response.json().catch(() => null);
  } else if (contentType.startsWith("text/")) {
    data = await response.text();
  }
  if (!response.ok) {
    const message = data && typeof data === "object" && data.error ? data.error : response.statusText;
    throw new Error(message || "请求失败");
  }
  return data;
}

export async function listStems() {
  const data = await request("/api/list-stems");
  if (!data || !Array.isArray(data.stems)) {
    throw new Error("返回的 stems 数据格式不正确");
  }
  return data.stems;
}

export async function fetchStem(stem) {
  if (!stem) {
    throw new Error("缺少 stem 参数");
  }
  const encoded = encodeURIComponent(stem);
  const data = await request(`/api/stem/${encoded}`);
  if (!data || typeof data.edl_text !== "string") {
    throw new Error("返回的 stem 数据不完整");
  }
  return data;
}

export async function saveEdl(stem, edlText) {
  const encoded = encodeURIComponent(stem);
  const response = await request(`/api/stem/${encoded}/save-edl`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: edlText,
  });
  if (!response?.ok) {
    throw new Error(response?.error || "保存 EDL 失败");
  }
  return response;
}

export async function saveCsv(stem, csvText) {
  const encoded = encodeURIComponent(stem);
  const response = await request(`/api/stem/${encoded}/save-csv`, {
    method: "POST",
    headers: { "Content-Type": "text/csv; charset=utf-8" },
    body: csvText,
  });
  if (!response?.ok) {
    throw new Error(response?.error || "保存 CSV 失败");
  }
  return response;
}

export async function uploadFile({ stem, type, file }) {
  const form = new FormData();
  form.append("stem", stem);
  form.append("type", type);
  form.append("file", file);
  const response = await request("/api/upload", {
    method: "POST",
    body: form,
  });
  if (!response?.ok) {
    throw new Error(response?.error || "上传失败");
  }
  return response;
}

