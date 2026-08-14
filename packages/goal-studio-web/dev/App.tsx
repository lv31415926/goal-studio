import { useEffect, useState } from "react";
import { api } from "../src/api/apiClient";
import type { SessionSummary } from "../src/types";
import { ReactFlowProvider } from "@xyflow/react";
import { Canvas } from "../src/components/Canvas/Canvas";
import { DialogPanel } from "../src/components/DialogPanel/DialogPanel";
import { NodeInspector } from "../src/components/NodeInspector/NodeInspector";
import { useCanvasStore } from "../src/stores/canvasStore";
import { useSession } from "../src/hooks/useSession";
import type { GoalContract } from "../src/types";
import "../src/styles/tokens.css";

/** Dev App — 整合三栏布局: Canvas | NodeInspector | DialogPanel */
export default function App() {
  const [goalText, setGoalText] = useState("创建一个代码审查技能");
  const [phase, setPhase] = useState<string>("init");
  const [status, setStatus] = useState<string>("就绪");

  // PR-ROUND-3/E-01: 顶层 useSession,与 DialogPanel 共享同一会话状态
  // 之前顶栏独立调用 api.createSession/draft/validate/build,与 DialogPanel 内 useSession 是两套独立状态
  const sessionState = useSession();
  const { session, loading, error, createAndDraft, loadSession, validate, build } = sessionState;

  // 会话列表 (加载已有案例)
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  useEffect(() => {
    api.listSessions().then(setSessions).catch(() => setSessions([]));
  }, []);

  const handleLoad = async (id: string) => {
    if (!id) return;
    setStatus("加载会话...");
    try {
      const s = await loadSession(id);
      if (s) setStatus(`已加载: ${s.name ?? s.id}`);
    } catch {
      setStatus("加载失败");
    }
  };

  const selectedNodeId = useCanvasStore((s) => s.selectedNodeId);

  const goal: GoalContract = {
    goal: goalText,
    prerequisites: [],
    constraints: [],
    success_criteria: ["生成SKILL.md"],
    product_type: "skill",
    metadata: {},
  };

  // E-01: 顶栏按钮复用共享 sessionState,避免与 DialogPanel 各持一套状态
  const handleDraft = async () => {
    setStatus("创建会话 + 起草 DAG...");
    try {
      await createAndDraft(goal, `dev-${Date.now()}`);
      setStatus("DAG 已生成,WebSocket 已连接");
    } catch {
      setStatus("创建失败 (详见对话面板错误)");
    }
  };

  const handleValidate = async () => {
    if (!session) return;
    setStatus("验证中...");
    try {
      const report = await validate();
      if (report) {
        setPhase("verify");
        setStatus(`验证: ${report.score.toFixed(1)} 分 / ${report.passed ? "通过" : "未通过"}`);
      }
    } catch {
      setStatus("验证失败 (详见对话面板错误)");
    }
  };

  const handleBuild = async () => {
    if (!session) return;
    setStatus("构建中...");
    try {
      const artifact = await build();
      if (artifact) {
        setPhase("build");
        setStatus(`构建完成: ${artifact.files["SKILL.md"]?.length ?? 0} 字符`);
      }
    } catch {
      setStatus("构建失败 (详见对话面板错误)");
    }
  };

  const phaseLabel: Record<string, { text: string; color: string }> = {
    init: { text: "初始化", color: "var(--gs-text-3)" },
    visualize: { text: "可视化", color: "var(--gs-info)" },
    refine: { text: "精化", color: "var(--gs-warn)" },
    verify: { text: "验证", color: "var(--gs-brand)" },
    build: { text: "构建", color: "var(--gs-success)" },
  };
  // 兜底: SessionPhase 有 8 个值,未在表中覆盖的 (draft/done/paused 等) 用灰色徽章
  // E-01: 优先使用共享 session 的 phase,回退到本地 phase
  const currentPhase = session?.phase ?? phase;
  const phaseInfo = phaseLabel[currentPhase] ?? { text: currentPhase, color: "#6b7280" };

  return (
    <div style={{
      display: "flex", flexDirection: "column", height: "100vh",
      fontFamily: "var(--gs-font)", background: "var(--gs-bg)", color: "var(--gs-text)",
    }}>
      {/* 顶栏 */}
      <header style={{
        padding: "var(--gs-space-3) var(--gs-space-5)",
        borderBottom: "1px solid var(--gs-border)",
        background: "var(--gs-surface)",
        boxShadow: "var(--gs-shadow-xs)",
        zIndex: 10,
      }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "var(--gs-space-2)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--gs-space-3)" }}>
            <div style={{
              width: 28, height: 28, borderRadius: "var(--gs-radius)",
              background: "linear-gradient(135deg, var(--gs-brand), var(--gs-layer-3))",
              display: "flex", alignItems: "center", justifyContent: "center",
              color: "#fff", fontWeight: 700, fontSize: 14,
            }}>G</div>
            <h1 style={{ margin: 0, fontSize: "var(--gs-text-lg)", fontWeight: 600 }}>
              goal-studio
              <span style={{ color: "var(--gs-text-3)", fontWeight: 400, fontSize: "var(--gs-text-sm)", marginLeft: 8 }}>
                可视化 Loop 创作系统
              </span>
            </h1>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "var(--gs-space-3)" }}>
            {session && (
              <span className="gs-badge" style={{ background: "var(--gs-surface-2)", color: "var(--gs-text-2)" }}>
                <code style={{ fontFamily: "var(--gs-font-mono)", fontSize: "var(--gs-text-xs)" }}>{session.id}</code>
              </span>
            )}
            <span className="gs-badge" style={{
              background: `${phaseInfo.color}1a`, color: phaseInfo.color,
            }}>
              {phaseInfo.text}
            </span>
          </div>
        </div>
        <div style={{ display: "flex", gap: "var(--gs-space-2)", alignItems: "center" }}>
          <select
            className="gs-input"
            style={{ width: 320 }}
            value={session?.id ?? ""}
            onChange={(e) => handleLoad(e.target.value)}
            disabled={loading}
          >
            <option value="">— 加载已有会话 ({sessions.length}) —</option>
            {sessions.map((it) => (
              <option key={it.id} value={it.id}>
                {(it.name || it.id).slice(0, 30)} · {it.phase} · {it.best_score.toFixed(1)}
              </option>
            ))}
          </select>
          <input
            type="text"
            value={goalText}
            onChange={(e) => setGoalText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && goalText.trim()) handleDraft(); }}
            placeholder="输入目标,例如:创建一个代码审查技能"
            className="gs-input"
            style={{ flex: 1 }}
          />
          <button
            onClick={handleDraft}
            disabled={loading || !goalText.trim()}
            className="gs-btn gs-btn-primary"
          >
            {loading ? "处理中..." : "起草 DAG"}
          </button>
          <button
            onClick={handleValidate}
            disabled={!session}
            className="gs-btn gs-btn-warn"
          >
            验证
          </button>
          <button
            onClick={handleBuild}
            disabled={!session}
            className="gs-btn gs-btn-success"
          >
            构建
          </button>
        </div>
        <div style={{ fontSize: "var(--gs-text-xs)", color: "var(--gs-text-3)", marginTop: "var(--gs-space-1)" }}>
          {error ?? status}
        </div>
      </header>

      {/* 三栏布局 */}
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        {/* 左: Canvas */}
        <div style={{
          flex: 3, borderRight: "1px solid var(--gs-border)",
          position: "relative", background: "var(--gs-surface)",
        }}>
          <ReactFlowProvider>
            <Canvas />
          </ReactFlowProvider>
        </div>

        {/* 中: NodeInspector */}
        <div style={{
          flex: "0 0 280px", borderRight: "1px solid var(--gs-border)",
          overflow: "auto", background: "var(--gs-surface-2)",
          display: "flex", flexDirection: "column",
        }}>
          <div style={{
            padding: "var(--gs-space-3) var(--gs-space-4)",
            borderBottom: "1px solid var(--gs-border)",
            fontWeight: 600, fontSize: "var(--gs-text-sm)",
            color: "var(--gs-text)", background: "var(--gs-surface)",
            display: "flex", alignItems: "center", justifyContent: "space-between",
          }}>
            <span>节点详情</span>
            {selectedNodeId && (
              <code style={{
                fontSize: "var(--gs-text-xs)", color: "var(--gs-brand)",
                fontFamily: "var(--gs-font-mono)",
              }}>{selectedNodeId}</code>
            )}
          </div>
          <div style={{ flex: 1, overflow: "auto" }}>
            <NodeInspector />
          </div>
        </div>

        {/* 右: DialogPanel */}
        <div style={{
          flex: "0 0 340px", display: "flex", flexDirection: "column",
          background: "var(--gs-surface)", borderLeft: "1px solid var(--gs-border)",
        }}>
          <div style={{
            padding: "var(--gs-space-3) var(--gs-space-4)",
            borderBottom: "1px solid var(--gs-border)",
            fontWeight: 600, fontSize: "var(--gs-text-sm)",
            color: "var(--gs-text)", background: "var(--gs-surface)",
          }}>
            对话交互
          </div>
          <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
            <DialogPanel
              goal={goal}
              onPhaseChange={setPhase}
              sessionState={sessionState}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
