
/**
 * useSession — 会话管理 + WebSocket 连接 hook
 *
 * 整合 REST API 与 WebSocket:
 *  - createAndDraft: 创建会话 + 起草 DAG + 连接 WS
 *  - sendIntent: 通过 WS 发送意图解析
 *  - 自动接收 graph_update / phase_change / intent_parsed 等消息
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Session } from "../api/apiClient";
import { useIntentParser } from "../stores/intentStore";
import { WSClient } from "../api/wsClient";
import { useCanvasStore } from "../stores/canvasStore";
import type { GoalContract, NodeGraph, Report, Artifact, SessionPhase } from "../types";
// R4-18: ParsedIntent 已提升至 types/index.ts (intentStore 重新导出,保持向后兼容)
import type { ParsedIntent } from "../types";

export function useSession() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // P-LOW-2-7: WS 连接状态(由 onStatusChange 驱动,含 reconnecting)
  const [wsStatus, setWsStatus] = useState<"disconnected" | "connecting" | "connected">("disconnected");
  // 边界场景: 删除级联确认请求 / 多模态冲突告警
  const [pendingConfirmation, setPendingConfirmation] = useState<Record<string, unknown> | null>(null);
  const [conflictWarning, setConflictWarning] = useState<string>("");
  // P1-4: 最近一次意图/消解结果,供 DialogPanel 展示
  const [lastIntent, setLastIntent] = useState<Record<string, unknown> | null>(null);
  const [lastResolve, setLastResolve] = useState<Record<string, unknown> | null>(null);
  // P-LOW-2-3: 预留 canUndo — 从 action_executed.undo_available 同步,PR-E 接入实际撤销逻辑
  const [canUndo, setCanUndo] = useState(false);
  const wsRef = useRef<WSClient | null>(null);
  // P1-3: 当前会话 ID ref,用于校验 WS 消息归属,避免旧会话消息串台
  const sessionIdRef = useRef<string | null>(null);
  // P-HIGH-6: createAndDraft 防重入 ref,避免快速双击创建多会话
  const creatingRef = useRef(false);
  // R4-10: final 消息版本号 ref,收到 final 时记录,晚到的 incremental 丢弃
  const finalVersionRef = useRef<number>(0);
  // R4-11: ASR 开始时快照当前 graph,供 ASR 结束后恢复
  const graphSnapshotRef = useRef<NodeGraph | null>(null);

  // Phase 3: 增量解析状态(语音 interim chunk 触发的低置信度预览)
  const { setIncremental, setParsed, clearIncremental } = useIntentParser();

  const setGraph = useCanvasStore((s) => s.setGraph);
  const setPhase = useCanvasStore((s) => s.setPhase);
  const setSessionId = useCanvasStore((s) => s.setSessionId);
  const selectNode = useCanvasStore((s) => s.selectNode);
  const hoverNode = useCanvasStore((s) => s.hoverNode);
  // R4-11: 画布锁定状态
  const setCanvasLocked = useCanvasStore((s) => s.setCanvasLocked);
  const currentGraph = useCanvasStore((s) => s.graph);

  // WebSocket 消息处理
  const handleWSMessage = useCallback(
    (type: string, payload: Record<string, unknown>, msgSessionId: string | null) => {
      // P1-3: 校验消息归属,旧会话的串台消息直接忽略 (session_id 由 wsClient 从顶层传入)
      if (msgSessionId && sessionIdRef.current && msgSessionId !== sessionIdRef.current) {
        return;
      }
      switch (type) {
        case "graph_update": {
          const graph = payload.graph as NodeGraph;
          if (graph) setGraph(graph);
          break;
        }
        case "phase_change": {
          const phase = payload.phase as string;
          if (phase) setPhase(phase as SessionPhase);
          break;
        }
        case "intent_parsed": {
          const intent = payload.intent as Record<string, unknown> | undefined;
          const version = (payload.version as number) ?? 0;
          // R4-09: 优先用 is_interim 作为唯一分流依据,向后兼容 parse_source 前缀
          const isInterim = intent?.is_interim as boolean | undefined;
          const source = (intent?.parse_source as string) ?? "";
          const shouldIncremental =
            isInterim === true ||
            (isInterim === undefined && source.startsWith("incremental_"));

          if (shouldIncremental) {
            // R4-10: 丢弃晚到的 incremental 消息 (版本号 < 已收到的 final 版本)
            if (version > 0 && version < finalVersionRef.current) {
              break;
            }
            setIncremental(intent as unknown as ParsedIntent | null);
          } else {
            // R4-10: 收到 final 消息时记录版本号
            if (version > finalVersionRef.current) {
              finalVersionRef.current = version;
            }
            setParsed(intent as unknown as ParsedIntent);
            // 收到最终解析后清空增量预览
            clearIncremental();
          }
          // P1-4: 暴露最近一次意图供 DialogPanel 展示
          setLastIntent(intent ?? null);
          break;
        }
        case "node_resolved": {
          // P1-4: 暴露最近一次消解结果供 DialogPanel 展示
          setLastResolve((payload.resolve as Record<string, unknown>) ?? null);
          break;
        }
        case "action_executed": {
          const graph = payload.graph as NodeGraph | null;
          const exec = (payload.execution as Record<string, unknown>) ?? {};
          // 边界场景: 删除级联确认 — graph 为 null 表示需前端确认
          if (exec.needs_confirmation && !graph) {
            setPendingConfirmation(exec.confirmation_context as Record<string, unknown> ?? {});
          } else {
            setPendingConfirmation(null);
          }
          // P-LOW-2-3: 同步 undo_available 供前端撤销按钮启用判断
          setCanUndo((exec.undo_available as boolean) ?? false);
          // 边界场景: 多模态冲突告警
          setConflictWarning((exec.conflict_warning as string) ?? "");
          if (graph) setGraph(graph);
          // P0-5: 执行失败(非确认/冲突场景)展示错误,避免静默丢弃
          if (
            exec.ok === false &&
            !exec.needs_confirmation &&
            !exec.conflict_warning
          ) {
            setError((exec.reason as string) || "执行失败");
          }
          break;
        }
        case "error": {
          const errMsg = payload.error as string;
          console.error("[WS error]", errMsg, payload.code);
          setError(errMsg);
          break;
        }
        case "pong":
          break;
        default:
      }
    },
    [setGraph, setPhase, setIncremental, setParsed, clearIncremental, setPendingConfirmation, setConflictWarning, setLastIntent, setLastResolve, setError],
  );

  // R4-08: handlerRef 存最新 handler,WSClient 通过 ref 调用,避免 stale closure
  const handlerRef = useRef(handleWSMessage);
  handlerRef.current = handleWSMessage;

  // 创建会话 + 起草 DAG + 连接 WS
  const createAndDraft = useCallback(
    async (goal: GoalContract, name = "") => {
      // P-HIGH-6: 防重入 — 快速双击时第二次调用直接返回
      if (creatingRef.current) return;
      creatingRef.current = true;
      setLoading(true);
      setError(null);
      setWsStatus("connecting");
      try {
        // 1. 创建会话
        const s = await api.createSession(name, goal);
        setSession(s);
        // P1-3/P1-5: 记录当前会话 ID (校验 WS 消息归属 + 供 NodeInspector 同步后端)
        sessionIdRef.current = s.id;
        setSessionId(s.id);

        // 2. 起草 DAG
        const graph = await api.draft(s.id, goal);
        setGraph(graph);
        setPhase("visualize");

        // 3. 连接 WebSocket
        if (wsRef.current) wsRef.current.close();
        // R4-08: 传入 ref 包装的 handler,确保 WSClient 始终调用最新 handler
        const ws = new WSClient(s.id, (type, payload, sid) =>
          handlerRef.current(type, payload, sid),
        );
        // P-LOW-2-7: 订阅 WS 状态变更,驱动 DialogPanel 的 wsStatus(含 reconnecting)
        ws.onStatusChange = (status) => {
          switch (status) {
            case "connected":
              setWsStatus("connected");
              break;
            case "reconnecting":
              setWsStatus("connecting");
              break;
            case "disconnected":
            case "reconnect_failed":
              setWsStatus("disconnected");
              break;
          }
        };
        ws.connect();
        wsRef.current = ws;

        const updated = await api.getSession(s.id);
        setSession(updated);
        // P-LOW-2-3: reload 后 ratchet.snapshots 为空,canUndo 必须重置为 false
        setCanUndo(false);
        return updated;
      } catch (e) {
        setError(String(e));
        setWsStatus("disconnected");
        throw e;
      } finally {
        setLoading(false);
        creatingRef.current = false;
      }
    },
    [setGraph, setPhase, setSessionId],
  );

  // 通过 WS 发送意图解析
  // force: 边界场景 — 确认后强制执行(跳过删除级联确认门控)
  const sendIntent = useCallback((text: string, isMobile = false, force = false) => {
    wsRef.current?.intentParse(text, isMobile, force);
  }, []);

  // Phase 3: 通过 WS 发送语音 chunk (interim/final) 触发后端增量解析
  const sendVoiceChunk = useCallback(
    (text: string, confidence: number, isFinal: boolean) => {
      wsRef.current?.voiceChunk(text, confidence, isFinal);
    },
    [],
  );

  // 发送鼠标选中
  const sendMouseSelect = useCallback(
    (nodeId: string | null) => {
      selectNode(nodeId);
      if (nodeId) wsRef.current?.mouseSelect(nodeId);
    },
    [selectNode],
  );

  // 发送鼠标悬停
  const sendMouseHover = useCallback(
    (nodeId: string | null) => {
      hoverNode(nodeId);
      wsRef.current?.mouseHover(nodeId);
    },
    [hoverNode],
  );

  // REST: refine — 后端返回 RatchetResponse,需从 result.graph 提取图
  const refine = useCallback(async (patch: import("../types").Patch) => {
    if (!session) return;
    const result = await api.refine(session.id, patch);
    if (result.graph) {
      setGraph(result.graph);
    }
    return result;
  }, [session, setGraph]);

  // REST: validate
  const validate = useCallback(async (): Promise<Report | null> => {
    if (!session) return null;
    const report = await api.validate(session.id);
    return report;
  }, [session]);

  // REST: build
  const build = useCallback(async (): Promise<Artifact | null> => {
    if (!session) return null;
    const artifact = await api.build(session.id);
    return artifact;
  }, [session]);

  // R4-11: ASR 开始时锁定画布状态 — 快照当前 graph 并设置 canvasLocked=true
  const beginVoiceInput = useCallback(() => {
    graphSnapshotRef.current = currentGraph;
    setCanvasLocked(true);
  }, [currentGraph, setCanvasLocked]);

  // R4-11: ASR 结束时解锁画布状态
  const endVoiceInput = useCallback(() => {
    setCanvasLocked(false);
  }, [setCanvasLocked]);

  // 清理
  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  return {
    session,
    loading,
    error,
    wsStatus,
    pendingConfirmation,
    conflictWarning,
    // P1-4: 最近一次意图/消解结果
    lastIntent,
    lastResolve,
    canUndo,
    setPendingConfirmation,
    createAndDraft,
    sendIntent,
    sendVoiceChunk,
    sendMouseSelect,
    sendMouseHover,
    refine,
    validate,
    build,
    // R4-11: ASR 画布锁定控制
    beginVoiceInput,
    endVoiceInput,
  };
}
