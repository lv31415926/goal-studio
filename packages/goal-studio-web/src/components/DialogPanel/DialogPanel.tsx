/**
 * DialogPanel - 对话/语音交互面板 (WebSocket 版)
 * 集成 useSession hook,支持文本/语音指令 + 实时消息接收
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useStreamTranscribe } from "../../hooks/useStreamTranscribe";
import { useIncrementalParse } from "../../hooks/useIncrementalParse";
import { useIntentParser } from "../../stores/intentStore";
import { useSession } from "../../hooks/useSession";
import type { GoalContract } from "../../types";

interface DialogPanelProps {
  goal: GoalContract | null;
  onPhaseChange?: (phase: string) => void;
  /** PR-ROUND-3/E-01: 外部注入共享 session 状态;未提供时内部自建(保持向后兼容) */
  sessionState?: ReturnType<typeof useSession>;
}

interface LogEntry {
  role: "user" | "system" | "assistant" | "error";
  content: string;
  time: number;
}

export function DialogPanel({ goal, onPhaseChange, sessionState: injected }: DialogPanelProps) {
  const [input, setInput] = useState("");
  const [dialogLog, setDialogLog] = useState<LogEntry[]>([]);
  const [validateResult, setValidateResult] = useState<string>("");
  const [buildResult, setBuildResult] = useState<string>("");
  // 边界场景: 命令/讨论模式 — push-to-talk 默认开,讨论模式不触发指令解析
  const [commandMode, setCommandMode] = useState<"command" | "discussion">("command");
  const logEndRef = useRef<HTMLDivElement>(null);
  // R4-16: 确认弹窗 focus trap — dialogRef 用于焦点管理
  const dialogRef = useRef<HTMLDivElement>(null);

  // PR-ROUND-3/E-01: 支持外部注入共享 session 状态;未注入时内部自建(保持向后兼容)
  // R4-05: useSession 的 WS 连接已在 createAndDraft 中惰性启动,即使 hook 被调用也不会创建 WS
  //        因此 fallback 模式不会影响主流程(未调用 createAndDraft 时 WS 不连接)
  const fallback = useSession();
  const sessionState = injected ?? fallback;
  const {
    session,
    loading,
    error,
    pendingConfirmation,
    conflictWarning,
    // P1-4: 消费最近一次意图/消解结果
    lastIntent,
    lastResolve,
    canUndo,
    wsStatus,
    setPendingConfirmation,
    createAndDraft,
    sendIntent,
    sendVoiceChunk,
    validate,
    build,
    // R4-11: ASR 画布锁定控制
    beginVoiceInput,
    endVoiceInput,
  } = sessionState;

  // Phase 3: 语音增量解析 — interim 节流推送,final 立即推送 + 触发完整意图解析
  const { handleChunk: handleVoiceChunk, dispose: disposeIncremental } = useIncrementalParse({
    sendVoiceChunk: session ? sendVoiceChunk : null,
    throttleMs: 250,
  });

  // R4-04: isListening 单一数据源 — 只使用 useStreamTranscribe 返回的 isListening (useState)
  //        移除 jotai isListeningAtom 的使用,避免双源不一致
  const { isListening, transcript, interimTranscript, start, stop, reset } = useStreamTranscribe({
    onChunk: (chunk, isFinal, confidence) => {
      // 1. 推入信号缓冲 + 通过 WS 发送 voice_chunk (节流)
      handleVoiceChunk(chunk, isFinal, confidence);
      // 2. final chunk: 追加到输入框,命令模式才触发完整 intent_parse
      if (isFinal) {
        setInput((prev) => prev + chunk);
        // 边界场景: 讨论模式只转录不解析,避免协作者说话被识别为指令
        if (session && commandMode === "command") sendIntent(chunk);
      }
    },
    // PR-ROUND-3/E-08: ASR 错误写入对话日志,避免静默丢弃
    onError: (e) => addLog("error", e.message),
  });
  // R4-04: 移除 setIsListening,只用 useStreamTranscribe 的 isListening 作为单一数据源
  const { incremental, clearIncremental } = useIntentParser();

  const addLog = useCallback((role: LogEntry["role"], content: string) => {
    // P1-10: 滑动窗口,仅保留最近 500 条避免无限增长
    setDialogLog((prev) => [...prev.slice(-500), { role, content, time: Date.now() }]);
  }, []);

  // 自动滚动到底部
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [dialogLog, transcript, interimTranscript]);

  // P0-5: 后端执行失败等错误写入对话日志展示,避免静默丢弃
  useEffect(() => {
    if (error) addLog("error", error);
  }, [error, addLog]);

  // 通知父组件阶段变化
  useEffect(() => {
    if (session && onPhaseChange) onPhaseChange(session.phase);
  }, [session?.phase, onPhaseChange]);

  // Phase 3: 卸载时清理增量解析节流定时器
  useEffect(() => {
    return () => disposeIncremental();
  }, [disposeIncremental]);

  const handleStart = useCallback(async () => {
    if (!goal) return;
    try {
      addLog("system", `创建会话: ${goal.goal}`);
      await createAndDraft(goal, `session-${Date.now()}`);
      addLog("system", "DAG 起草完成,WebSocket 已连接");
    } catch (e) {
      addLog("error", String(e));
    }
  }, [goal, createAndDraft, addLog]);

  // R4-04: handleSend 发送文本前调用 stop() 停止录音
  const handleSend = useCallback(() => {
    if (!input.trim() || !session) return;
    stop();  // R4-04: 停止录音,避免发送文本时仍在录音
    addLog("user", input);
    sendIntent(input);
    setInput("");
    reset();
    clearIncremental();
  }, [input, session, sendIntent, addLog, reset, clearIncremental, stop]);

  // R4-04: isListening 单一数据源 — start()/stop() 内部已管理 isListening state
  //        移除多余的 setIsListening 调用
  // R4-11: ASR 开始/结束时锁定/解锁画布
  const handleVoiceToggle = useCallback(() => {
    if (isListening) {
      stop();
      disposeIncremental();
      endVoiceInput();  // R4-11: 解锁画布
    } else {
      // 边界场景: 讨论模式提示(仍允许转录,但不触发指令)
      beginVoiceInput();  // R4-11: 锁定画布,快照当前 graph
      start();
    }
  }, [isListening, start, stop, disposeIncremental, beginVoiceInput, endVoiceInput]);

  // 边界场景: 确认删除级联 — 重新发送带 force=true 的指令
  const handleConfirmDelete = useCallback(() => {
    if (!pendingConfirmation || !session) return;
    const ctx = pendingConfirmation as Record<string, unknown>;
    const targetId = ctx.target_node_id as string;
    // 重新发送删除指令,带 force
    sendIntent(`删除 ${targetId}`, false, true);
    setPendingConfirmation(null);
    addLog("system", `已确认删除 ${ctx.target_title ?? targetId} 及其级联节点`);
  }, [pendingConfirmation, session, sendIntent, setPendingConfirmation, addLog]);

  const handleCancelConfirm = useCallback(() => {
    setPendingConfirmation(null);
    addLog("system", "已取消删除操作");
  }, [setPendingConfirmation, addLog]);

  // R4-16: 确认弹窗 Focus Trap — 弹窗打开时将焦点移入弹窗
  useEffect(() => {
    if (pendingConfirmation && dialogRef.current) {
      dialogRef.current.focus();
    }
  }, [pendingConfirmation]);

  // R4-16: 弹窗内 Tab 键循环 + Esc 关闭
  const handleDialogKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      handleCancelConfirm();
      return;
    }
    if (e.key === "Tab") {
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = dialog.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
  }, [handleCancelConfirm]);

  // E-10: a11y — 删除确认弹窗打开时监听 Esc 键关闭 (保留全局 Esc 监听作为后备)
  useEffect(() => {
    if (!pendingConfirmation) return;
    const onKeydown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        handleCancelConfirm();
      }
    };
    window.addEventListener("keydown", onKeydown);
    return () => window.removeEventListener("keydown", onKeydown);
  }, [pendingConfirmation, handleCancelConfirm]);

  const handleValidate = useCallback(async () => {
    if (!session) return;
    try {
      const report = await validate();
      if (report) {
        setValidateResult(
          `${report.score.toFixed(1)} 分 · ${report.passed ? "通过" : "未通过"}\n` +
          (report.issues.length ? `问题: ${report.issues.join("; ")}` : "") +
          (report.suggestions.length ? `\n建议: ${report.suggestions.join("; ")}` : "")
        );
        addLog("system", `验证完成: ${report.score.toFixed(1)} 分`);
      }
    } catch (e) {
      setValidateResult(`验证失败: ${e}`);
    }
  }, [session, validate, addLog]);

  const handleBuild = useCallback(async () => {
    if (!session) return;
    try {
      const artifact = await build();
      if (artifact) {
        setBuildResult(artifact.content.slice(0, 200));
        addLog("system", `构建完成: ${artifact.files["SKILL.md"]?.length ?? 0} 字符`);
      }
    } catch (e) {
      setBuildResult(`构建失败: ${e}`);
    }
  }, [session, build, addLog]);

  const wsColor = wsStatus === "connected" ? "var(--gs-success)" : wsStatus === "connecting" ? "var(--gs-warn)" : "var(--gs-text-3)";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* 会话状态条 */}
      <div style={{
        padding: "var(--gs-space-2) var(--gs-space-4)",
        borderBottom: "1px solid var(--gs-border)",
        background: "var(--gs-surface-2)",
        fontSize: "var(--gs-text-xs)",
        color: "var(--gs-text-2)",
        display: "flex", alignItems: "center", justifyContent: "space-between",
      }}>
        <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: wsColor }} />
          {wsStatus === "connected" ? "已连接" : wsStatus === "connecting" ? "连接中..." : "未连接"}
        </span>
        {session && (
          <span>阶段 <strong style={{ color: "var(--gs-brand)" }}>{session.phase}</strong> · 最佳 <strong>{session.best_score.toFixed(1)}</strong></span>
        )}
      </div>

      {/* 对话日志 */}
      <div style={{
        flex: 1, overflow: "auto", padding: "var(--gs-space-3) var(--gs-space-4)",
        display: "flex", flexDirection: "column", gap: "var(--gs-space-2)",
      }}>
        {dialogLog.length === 0 && !transcript && (
          <div style={{
            color: "var(--gs-text-3)", fontSize: "var(--gs-text-sm)", textAlign: "center",
            padding: "var(--gs-space-6) 0",
          }}>
            {session ? "输入指令修改工作流,如「把 l1-req 改成异步」" : "点击下方按钮创建会话并起草 DAG"}
          </div>
        )}
        {dialogLog.map((d, i) => (
          <div key={i} style={{
            display: "flex", justifyContent: d.role === "user" ? "flex-end" : "flex-start",
          }}>
            <div style={{
              maxWidth: "85%", padding: "6px 10px", borderRadius: "var(--gs-radius)",
              fontSize: "var(--gs-text-sm)", lineHeight: 1.4,
              background: d.role === "user" ? "var(--gs-brand)" :
                         d.role === "error" ? "var(--gs-danger-soft)" :
                         d.role === "assistant" ? "var(--gs-brand-soft)" : "var(--gs-surface-2)",
              color: d.role === "user" ? "var(--gs-text-inverse)" :
                     d.role === "error" ? "var(--gs-danger)" : "var(--gs-text)",
            }}>
              {d.content}
            </div>
          </div>
        ))}
        {interimTranscript && (
          <div style={{
            alignSelf: "center", padding: "4px 10px", borderRadius: "var(--gs-radius)",
            background: "var(--gs-surface-2)", color: "var(--gs-text-3)",
            fontSize: "var(--gs-text-xs)", fontStyle: "italic",
          }}>
            转录中: {interimTranscript}
          </div>
        )}
        {transcript && (
          <div style={{
            alignSelf: "center", padding: "4px 10px", borderRadius: "var(--gs-radius)",
            background: "var(--gs-info-soft)", color: "var(--gs-info)",
            fontSize: "var(--gs-text-xs)", fontStyle: "italic",
          }}>
            语音: {transcript}
          </div>
        )}
        {incremental && incremental.intent && (
          <div style={{
            padding: "6px 10px", borderRadius: "var(--gs-radius)",
            background: "var(--gs-surface-2)", color: "var(--gs-text-2)",
            fontSize: "var(--gs-text-xs)", fontFamily: "var(--gs-font-mono)",
            border: "1px dashed var(--gs-border)",
          }}>
            增量预览: {String(incremental.intent)}
            {incremental.target_ref ? ` → ${String(incremental.target_ref.value)}` : ""}
            {incremental.parse_confidence ? ` (${(incremental.parse_confidence * 100).toFixed(0)}%)` : ""}
          </div>
        )}
        {lastIntent && (
          <div style={{
            padding: "6px 10px", borderRadius: "var(--gs-radius)",
            background: "var(--gs-brand-soft)", color: "var(--gs-brand)",
            fontSize: "var(--gs-text-xs)", fontFamily: "var(--gs-font-mono)",
          }}>
            意图: {JSON.stringify(lastIntent)}
          </div>
        )}
        {lastResolve && (
          <div style={{
            padding: "6px 10px", borderRadius: "var(--gs-radius)",
            background: "var(--gs-success-soft)", color: "var(--gs-success)",
            fontSize: "var(--gs-text-xs)", fontFamily: "var(--gs-font-mono)",
          }}>
            消解: {JSON.stringify(lastResolve)}
          </div>
        )}
        {validateResult && (
          <pre style={{
            padding: "8px 10px", borderRadius: "var(--gs-radius)",
            background: "var(--gs-warn-soft)", color: "var(--gs-text)",
            fontSize: "var(--gs-text-xs)", whiteSpace: "pre-wrap",
            fontFamily: "var(--gs-font-mono)", margin: 0,
          }}>
            {validateResult}
          </pre>
        )}
        {buildResult && (
          <pre style={{
            padding: "8px 10px", borderRadius: "var(--gs-radius)",
            background: "var(--gs-success-soft)", color: "var(--gs-text)",
            fontSize: "var(--gs-text-xs)", whiteSpace: "pre-wrap",
            fontFamily: "var(--gs-font-mono)", margin: 0, maxHeight: 120, overflow: "auto",
          }}>
            {buildResult}
          </pre>
        )}
        {/* 边界场景: 多模态冲突告警 */}
        {conflictWarning && (
          <div style={{
            padding: "6px 10px", borderRadius: "var(--gs-radius)",
            background: "var(--gs-warn-soft)", color: "var(--gs-warn)",
            fontSize: "var(--gs-text-xs)", lineHeight: 1.4,
            border: "1px solid var(--gs-warn)",
          }}>
            ⚠ {conflictWarning}
          </div>
        )}
        {/* 边界场景: 删除级联确认弹窗 (R4-16: Focus Trap + Tab 循环) */}
        {pendingConfirmation && (pendingConfirmation as Record<string, unknown>).op === "delete" && (
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-confirm-title"
            tabIndex={-1}
            onKeyDown={handleDialogKeyDown}
            style={{
              padding: "10px", borderRadius: "var(--gs-radius)",
              background: "var(--gs-danger-soft)", border: "1px solid var(--gs-danger)",
              fontSize: "var(--gs-text-sm)",
              outline: "none",
            }}
          >
            <div id="delete-confirm-title" style={{ fontWeight: 600, color: "var(--gs-danger)", marginBottom: 6 }}>
              确认删除?
            </div>
            <div style={{ color: "var(--gs-text)", marginBottom: 4 }}>
              删除「{String((pendingConfirmation as Record<string, unknown>).target_title ?? "")}」将级联删除以下节点:
            </div>
            <div style={{ fontSize: "var(--gs-text-xs)", color: "var(--gs-text-2)", marginBottom: 8, maxHeight: 80, overflow: "auto" }}>
              {((pendingConfirmation as Record<string, unknown>).cascade_nodes as Array<Record<string, unknown>> ?? [])
                .map((n, i) => `${i + 1}. ${n.title} (${n.id})`).join("  ·  ")}
            </div>
            <div style={{ display: "flex", gap: "var(--gs-space-2)" }}>
              <button onClick={handleConfirmDelete} className="gs-btn gs-btn-danger" style={{ flex: 1, justifyContent: "center", fontSize: "var(--gs-text-xs)" }}>
                确认删除
              </button>
              <button onClick={handleCancelConfirm} className="gs-btn" style={{ flex: 1, justifyContent: "center", fontSize: "var(--gs-text-xs)" }}>
                取消
              </button>
            </div>
          </div>
        )}
        {/* 边界场景: 循环依赖断边提示 (R4-16: Focus Trap) */}
        {pendingConfirmation && (pendingConfirmation as Record<string, unknown>).op === "connect" && (
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="connect-warn-title"
            tabIndex={-1}
            onKeyDown={handleDialogKeyDown}
            style={{
              padding: "10px", borderRadius: "var(--gs-radius)",
              background: "var(--gs-warn-soft)", border: "1px solid var(--gs-warn)",
              fontSize: "var(--gs-text-sm)",
              outline: "none",
            }}
          >
            <div id="connect-warn-title" style={{ fontWeight: 600, color: "var(--gs-warn)", marginBottom: 6 }}>
              循环依赖
            </div>
            <div style={{ color: "var(--gs-text)", marginBottom: 4 }}>
              此连接会形成环,请断开以下路径中的一条边:
            </div>
            <code style={{ fontSize: "var(--gs-text-xs)", color: "var(--gs-text-2)", fontFamily: "var(--gs-font-mono)" }}>
              {((pendingConfirmation as Record<string, unknown>).cycle_path as string[] ?? []).join(" → ")}
            </code>
            <div style={{ marginTop: 8 }}>
              <button onClick={handleCancelConfirm} className="gs-btn" style={{ fontSize: "var(--gs-text-xs)" }}>
                知道了
              </button>
            </div>
          </div>
        )}
        <div ref={logEndRef} />
      </div>

      {/* 操作按钮区 */}
      {!session ? (
        <div style={{ padding: "var(--gs-space-3) var(--gs-space-4)", borderTop: "1px solid var(--gs-border)" }}>
          <button
            onClick={handleStart}
            disabled={!goal || loading}
            className="gs-btn gs-btn-primary"
            style={{ width: "100%", justifyContent: "center", padding: "10px" }}
          >
            {loading ? "创建中..." : "创建会话 + 起草 DAG"}
          </button>
        </div>
      ) : (
        <div style={{
          padding: "var(--gs-space-2) var(--gs-space-4)",
          borderTop: "1px solid var(--gs-border)",
          display: "flex", gap: "var(--gs-space-2)",
        }}>
          <button
            onClick={() => addLog("system", canUndo ? "撤销功能将在 PR-E 接入" : "无可撤销操作")}
            disabled={!canUndo}
            title={canUndo ? "撤销上一步操作" : "无可撤销操作(reload 后撤销不可用)"}
            className="gs-btn"
            style={{ flexShrink: 0, opacity: canUndo ? 1 : 0.4 }}
          >
            撤销
          </button>
          <button onClick={handleValidate} className="gs-btn gs-btn-warn" style={{ flex: 1, justifyContent: "center" }}>
            验证
          </button>
          <button onClick={handleBuild} className="gs-btn gs-btn-success" style={{ flex: 1, justifyContent: "center" }}>
            构建
          </button>
        </div>
      )}

      {/* 输入区 */}
      <div style={{
        padding: "var(--gs-space-3) var(--gs-space-4)",
        borderTop: "1px solid var(--gs-border)",
        display: "flex", gap: "var(--gs-space-2)", alignItems: "center",
      }}>
        <button
          onClick={() => setCommandMode((m) => (m === "command" ? "discussion" : "command"))}
          disabled={!session}
          title={commandMode === "command" ? "命令模式(语音触发指令),点击切换为讨论模式" : "讨论模式(仅转录不触发指令),点击切换为命令模式"}
          style={{
            width: 36, height: 36, borderRadius: "var(--gs-radius)", border: "none",
            background: commandMode === "command" ? "var(--gs-brand-soft)" : "var(--gs-surface-2)",
            color: commandMode === "command" ? "var(--gs-brand)" : "var(--gs-text-3)",
            cursor: session ? "pointer" : "not-allowed", fontSize: 14, flexShrink: 0,
          }}
          aria-label="切换命令/讨论模式"
        >
          {commandMode === "command" ? "⌘" : "💬"}
        </button>
        <button
          onClick={handleVoiceToggle}
          disabled={!session}
          style={{
            width: 36, height: 36, borderRadius: "var(--gs-radius)", border: "none",
            background: isListening ? "var(--gs-danger)" : "var(--gs-surface-2)",
            color: isListening ? "var(--gs-text-inverse)" : "var(--gs-text-2)",
            cursor: session ? "pointer" : "not-allowed", fontSize: 16,
            transition: "all var(--gs-transition)", flexShrink: 0,
          }}
          aria-label={isListening ? "停止语音输入" : "开始语音输入"}
        >
          {isListening ? "■" : "🎤"}
        </button>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleSend(); }}
          placeholder={session ? "输入指令,如:把 l1-req 改成异步" : "请先创建会话"}
          disabled={!session}
          className="gs-input"
          aria-label="输入指令"
        />
        <button
          onClick={handleSend}
          disabled={!session || !input.trim()}
          className="gs-btn gs-btn-primary"
          style={{ flexShrink: 0 }}
        >
          发送
        </button>
      </div>
    </div>
  );
}
