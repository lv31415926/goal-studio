/**
 * WebSocket 客户端 — 与 goal-studio-server /ws/sessions/{id} 对接
 *
 * 消息协议见 goal-studio-server/src/goal_studio_server/ws_protocol.py
 */
type ServerMessageHandler = (
  type: string,
  payload: Record<string, unknown>,
  sessionId: string | null,
) => void;

export interface ServerMessage {
  type: string;
  payload: Record<string, unknown>;
  session_id: string | null;
  timestamp: number;
}

/** P0-3: 发送队列上限,超过则丢弃最旧消息 */
const MAX_PENDING_QUEUE = 50;
/** P1-1: pong 超时阈值 (ms),超过则判定半开连接并触发重连 */
const PONG_TIMEOUT = 45000;

// R4-23: WS 基址默认相对路径 (从 window.location 推导),移除硬编码 127.0.0.1:8000
function buildWsUrl(sessionId: string): string {
  const wsBase = import.meta.env.VITE_GOAL_STUDIO_WS_BASE ||
    `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
  const token = import.meta.env.VITE_GOAL_STUDIO_API_TOKEN || localStorage.getItem("gs_token");
  const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${wsBase}/sessions/${sessionId}${tokenParam}`;
}

export class WSClient {
  private ws: WebSocket | null = null;
  private url: string;
  private handler: ServerMessageHandler;
  private reconnectAttempts = 0;
  private maxReconnect = 5;
  private shouldReconnect = true;
  private pingInterval: ReturnType<typeof setInterval> | null = null;
  // P0-2: 重连定时器引用,close() 时需清除以避免资源泄漏
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  // PR-ROUND-3/E-06: 连接 generation,用于 onclose 判断 ws 引用一致性
  private generation = 0;
  // P0-3: 非 OPEN 状态发送队列,onopen 后 flush
  private pendingQueue: Array<{ type: string; payload: unknown }> = [];
  // P1-1: 最近一次收到 pong 的时间,用于半开连接检测
  private lastPongAt: number = Date.now();
  // P1-2: 连接状态变更回调(重连耗尽时通知 UI)
  onStatusChange?: (status: "connected" | "disconnected" | "reconnecting" | "reconnect_failed") => void;

  constructor(sessionId: string, handler: ServerMessageHandler) {
    this.url = buildWsUrl(sessionId);
    this.handler = handler;
  }

  connect(): void {
    // PR-ROUND-3/E-05: 防止多次调用创建多个 WebSocket
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.shouldReconnect = true;
    this._doConnect();
  }

  reconnect(): void {
    // PR-ROUND-3/E-07: 重置重连状态,允许重连耗尽后手动重试
    this.shouldReconnect = true;
    this.reconnectAttempts = 0;
    // E-06: 关闭旧连接并递增 generation,使旧 onclose 回调失效
    this.generation += 1;
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this._doConnect();
  }

  private _doConnect(): void {
    // E-06: 递增 generation,用于 onclose 判断是否为当前连接
    this.generation += 1;
    const gen = this.generation;
    try {
      this.ws = new WebSocket(this.url);
    } catch (e) {
      console.error("[WS] connect error:", e);
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log("[WS] connected");
      this.reconnectAttempts = 0;
      this.lastPongAt = Date.now();
      // P-LOW-2-7: 连接成功通知 UI
      this.onStatusChange?.("connected");
      // 启动心跳 (P1-1: 内含 pong 超时检测)
      this.pingInterval = setInterval(() => {
        if (Date.now() - this.lastPongAt > PONG_TIMEOUT) {
          console.warn("[WS] pong timeout, closing to trigger reconnect");
          this.ws?.close();
          return;
        }
        this.send("ping", {});
      }, 30000);
      // P0-3: 连接建立后 flush 暂存队列
      this._flushPending();
    };

    this.ws.onmessage = (ev) => {
      try {
        const msg: ServerMessage = JSON.parse(ev.data);
        // P1-1: 记录 pong 时间用于半开检测
        if (msg.type === "pong") {
          this.lastPongAt = Date.now();
        }
        this.handler(msg.type, msg.payload, msg.session_id ?? null);
      } catch (e) {
        console.error("[WS] parse error:", e, ev.data);
      }
    };

    this.ws.onerror = (e) => {
      console.error("[WS] error:", e);
    };

    this.ws.onclose = (ev) => {
      // PR-ROUND-3/E-06: 检查 generation,旧连接的 onclose 不处理(避免重连/状态串台)
      if (gen !== this.generation) return;
      console.log("[WS] closed:", ev.code, ev.reason);
      if (this.pingInterval) {
        clearInterval(this.pingInterval);
        this.pingInterval = null;
      }
      // R4-15: 认证失败 (code 1008 policy violation, 或 4000-4100 自定义认证错误) 不重连
      if (ev.code === 1008 || (ev.code >= 4000 && ev.code <= 4100)) {
        this.shouldReconnect = false;
        this.onStatusChange?.("disconnected");
        return;
      }
      // P-LOW-2-7: 区分 reconnecting/reconnect_failed/disconnected(专家 4: 避免 UI 闪烁)
      if (this.shouldReconnect && this.reconnectAttempts < this.maxReconnect) {
        this.onStatusChange?.("reconnecting");
        this._scheduleReconnect();
      } else if (this.shouldReconnect && this.reconnectAttempts >= this.maxReconnect) {
        this.onStatusChange?.("reconnect_failed");
      } else {
        this.onStatusChange?.("disconnected");
      }
    };
  }

  private _scheduleReconnect(): void {
    this.reconnectAttempts += 1;
    // R4-14: 指数退避加 jitter (随机抖动 0-500ms),避免多个客户端同步重连
    const baseDelay = Math.min(1000 * 2 ** this.reconnectAttempts, 16000);
    const delay = baseDelay + Math.random() * 500;
    console.log(`[WS] reconnect in ${delay}ms (attempt ${this.reconnectAttempts})`);
    // P0-2: 记录 timer 引用供 close() 清除
    this.reconnectTimer = setTimeout(() => this._doConnect(), delay);
  }

  send(type: string, payload: Record<string, unknown> = {}): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type, payload }));
      return;
    }
    // P0-3: 非 OPEN 状态入队,避免静默丢弃;超过上限丢弃最旧
    this.pendingQueue.push({ type, payload });
    if (this.pendingQueue.length > MAX_PENDING_QUEUE) {
      this.pendingQueue.shift();
    }
  }

  /** P0-3: 连接建立后 flush 暂存队列 */
  private _flushPending(): void {
    while (this.pendingQueue.length > 0) {
      const item = this.pendingQueue.shift();
      if (item && this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: item.type, payload: item.payload }));
      }
    }
  }

  /** 解析自然语言指令 (五层流水线全流程)
   * force: 边界场景 — 确认后强制执行(跳过删除级联确认门控)
   */
  intentParse(text: string, isMobile = false, force = false): void {
    this.send("intent_parse", { text, is_mobile: isMobile, force });
  }

  /** 流式 ASR chunk (增量解析) */
  voiceChunk(text: string, confidence = 0.9, isFinal = false): void {
    this.send("voice_chunk", { text, confidence, is_final: isFinal });
  }

  /** 鼠标选中节点 (Layer 2 信号) */
  mouseSelect(nodeId: string): void {
    this.send("mouse_select", { node_id: nodeId });
  }

  /** 鼠标悬停节点 (Layer 2 信号) */
  mouseHover(nodeId: string | null): void {
    this.send("mouse_hover", { node_id: nodeId });
  }

  close(): void {
    this.shouldReconnect = false;
    // P0-2: 清除挂起的重连定时器,避免 close 后仍重连
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
    // P0-3: close 时清空未发送队列,避免下次复用串台
    this.pendingQueue = [];
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }
}
