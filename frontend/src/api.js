import { accessToken, keycloak } from "./auth";

export async function authorizedFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = await accessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  // The server selects the sole Yunnan workspace; clients cannot choose one.
  headers.delete("X-Project-Id");
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401 && path.startsWith("/api/")) {
    keycloak.login();
  }
  return response;
}

export async function request(path, options = {}) {
  const response = await authorizedFetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(readableError(response.status, body));
    error.status = response.status;
    error.transient = response.status === 502 || response.status === 503 || response.status === 504
      || (typeof body === "string" && (/^<!?doctype html/i.test(body.trim()) || /^<html/i.test(body.trim())));
    throw error;
  }
  return body;
}

function readableError(status, body) {
  if (typeof body !== "string") return body?.detail || "请求失败";
  const text = body.trim();
  if (status === 504 || /^<!?doctype html/i.test(text) || /^<html/i.test(text)) {
    return "服务器入口等待超时，系统正在尝试恢复连接。";
  }
  return text || "请求失败";
}

export async function streamJsonLines(path, options = {}, onEvent = () => {}) {
  const response = await authorizedFetch(path, options);
  if (!response.ok) {
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("application/json") ? await response.json() : await response.text();
    throw new Error(readableError(response.status, body));
  }
  if (!response.body) throw new Error("浏览器不支持读取目标理解进度，请刷新后重试。");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result;

  function consume(line) {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    onEvent(event);
    if (event.type === "error") throw new Error(event.message || "目标理解失败，请重试。");
    if (event.type === "result") result = event.payload;
  }

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    lines.forEach(consume);
    if (done) break;
  }
  consume(buffer);
  if (!result) throw new Error("目标理解未返回完整草稿，请重试。");
  return result;
}

export function jsonRequest(method, body) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}
