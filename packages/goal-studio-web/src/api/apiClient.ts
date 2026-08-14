/**
 * REST API 客户端 — 与 goal-studio-server 对接
 */
import type {
  GoalContract,
  NodeGraph,
  Patch,
  Report,
  Artifact,
  SessionSummary,
  AdapterInfo,
  RatchetResponse,
} from "../types";

// R4-23: API_BASE 默认相对路径 "/api" (SPA 与后端同源),移除硬编码 127.0.0.1:8000
const API_BASE = import.meta.env.VITE_GOAL_STUDIO_API_BASE || "/api";
// P-LOW-2-6: 请求超时 15s,避免 LLM 长时间阻塞前端
const REQUEST_TIMEOUT_MS = 15000;

/** P-LOW-2-6: 结构化 API 错误(含 status/code,供前端区分超时/网络/HTTP) */
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface Session {
  id: string;
  name: string;
  goal: GoalContract | null;
  graph: NodeGraph | null;
  phase: string;
  best_score: number;
  ratchet_history: Array<Record<string, unknown>>;
  dialog: Array<{ role: string; content: string }>;
  created_at: string;
  updated_at: string;
  metadata: Record<string, unknown>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = import.meta.env.VITE_GOAL_STUDIO_API_TOKEN || localStorage.getItem("gs_token");
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  // P-LOW-2-6: AbortController 实现超时控制
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const resp = await fetch(`${API_BASE}${path}`, {
      headers,
      ...init,
      signal: controller.signal,
    });
    if (!resp.ok) {
      const text = await resp.text();
      // 尝试解析后端错误码
      let code = "http_error";
      try {
        const j = JSON.parse(text);
        code = j.code || j.detail || code;
      } catch {
        // 非 JSON 错误体,保留默认 code
      }
      // R4-13: 错误消息截断到 200 字符,避免过长错误体泄露敏感信息
      const truncatedText = text.length > 200 ? text.slice(0, 200) + "..." : text;
      throw new ApiError(resp.status, code, `API ${resp.status}: ${truncatedText}`);
    }
    // R4-12: 204 No Content 响应跳过 JSON 解析,直接返回 undefined
    if (resp.status === 204) {
      return undefined as T;
    }
    return resp.json() as Promise<T>;
  } catch (e) {
    // ApiError 直接抛出
    if (e instanceof ApiError) throw e;
    // AbortError → 超时
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new ApiError(0, "timeout", `请求超时 (${REQUEST_TIMEOUT_MS}ms)`);
    }
    // 其他网络错误
    throw new ApiError(0, "network", String(e));
  } finally {
    clearTimeout(timer);
  }
}

export const api = {
  health: () => request<{ status: string; version: string }>("/health"),

  listAdapters: () => request<AdapterInfo[]>("/adapters"),

  listSessions: () => request<SessionSummary[]>("/sessions"),

  createSession: (name: string, goal?: Partial<GoalContract>) =>
    request<Session>("/sessions", {
      method: "POST",
      body: JSON.stringify({ name, goal: goal ?? null }),
    }),

  getSession: (id: string) => request<Session>(`/sessions/${id}`),

  deleteSession: (id: string) =>
    request<{ ok: boolean }>(`/sessions/${id}`, { method: "DELETE" }),

  draft: (sessionId: string, goal: GoalContract) =>
    request<NodeGraph>(`/sessions/${sessionId}/draft`, {
      method: "POST",
      body: JSON.stringify({ goal }),
    }),

  refine: (sessionId: string, patch: Patch) =>
    request<RatchetResponse>(`/sessions/${sessionId}/refine`, {
      method: "POST",
      body: JSON.stringify({ patch }),
    }),

  validate: (sessionId: string) =>
    request<Report>(`/sessions/${sessionId}/validate`, { method: "POST" }),

  build: (sessionId: string) =>
    request<Artifact>(`/sessions/${sessionId}/build`, { method: "POST" }),
};
