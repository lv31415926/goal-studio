/**
 * NodeInspector - 节点详情编辑侧边栏
 * 展示选中节点的属性和槽位,支持文本编辑
 *
 * P1-5: onChange 只更新本地草稿,debounce(300ms)/onBlur 后调用 api.refine 同步后端,
 * 避免编辑仅停留在本地 store 造成数据丢失。
 */
import { useEffect, useRef, useState } from "react";
import { useCanvasStore } from "../../stores/canvasStore";
import { api } from "../../api/apiClient";
import type { Patch } from "../../types";

const LAYER_LABELS: Record<number, { text: string; color: string }> = {
  1: { text: "L1 规划层", color: "var(--gs-layer-1)" },
  2: { text: "L2 分配层", color: "var(--gs-layer-2)" },
  3: { text: "L3 精化层", color: "var(--gs-layer-3)" },
};

const STATUS_LABELS: Record<string, { text: string; color: string; bg: string }> = {
  draft: { text: "草稿", color: "var(--gs-node-draft)", bg: "var(--gs-surface-3)" },
  active: { text: "进行中", color: "var(--gs-node-active)", bg: "var(--gs-brand-soft)" },
  done: { text: "完成", color: "var(--gs-node-done)", bg: "var(--gs-success-soft)" },
  error: { text: "错误", color: "var(--gs-node-error)", bg: "var(--gs-danger-soft)" },
  pending: { text: "待处理", color: "var(--gs-node-pending)", bg: "var(--gs-warn-soft)" },
};

export function NodeInspector() {
  const graph = useCanvasStore((s) => s.graph);
  const selectedId = useCanvasStore((s) => s.selectedNodeId);
  const sessionId = useCanvasStore((s) => s.sessionId);
  const setGraph = useCanvasStore((s) => s.setGraph);

  // P1-5: 本地草稿状态,onChange 只更新本地,避免每键都打后端
  const [titleDraft, setTitleDraft] = useState("");
  const [descDraft, setDescDraft] = useState("");
  // PR-ROUND-3/E-09: 保存错误状态 + 保存中状态,供 UI 反馈
  const [saveError, setSaveError] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const refineTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 记录上次同步草稿的节点 ID,仅选中节点变化时同步,避免每次 graph 更新打断输入
  const prevSelectedRef = useRef<string | null>(null);

  // 选中节点变化时同步草稿(graph 后到时也会触发,但仅当 selectedId 变化才真正写入)
  useEffect(() => {
    if (!selectedId || !graph) {
      prevSelectedRef.current = selectedId;
      return;
    }
    if (prevSelectedRef.current !== selectedId) {
      const n = graph.nodes[selectedId];
      if (n) {
        setTitleDraft(n.title);
        setDescDraft(n.description);
      }
      prevSelectedRef.current = selectedId;
    }
  }, [selectedId, graph]);

  // 卸载时清理 debounce 定时器
  useEffect(() => {
    return () => {
      if (refineTimerRef.current) {
        clearTimeout(refineTimerRef.current);
        refineTimerRef.current = null;
      }
    };
  }, []);

  if (!graph || !selectedId) {
    return (
      <div style={{
        padding: "var(--gs-space-5) var(--gs-space-4)",
        color: "var(--gs-text-3)", fontSize: "var(--gs-text-sm)", textAlign: "center",
      }}>
        <div style={{ fontSize: 32, marginBottom: "var(--gs-space-2)", opacity: 0.4 }}>⊙</div>
        选择一个节点查看详情
      </div>
    );
  }

  const node = graph.nodes[selectedId];
  if (!node) return null;

  const layerCfg = LAYER_LABELS[node.layer] || LAYER_LABELS[1];
  const statusCfg = STATUS_LABELS[node.status] || STATUS_LABELS.draft;

  const makePatch = (slots: Record<string, unknown>): Patch => ({
    op: "update_node",
    target_node_id: selectedId,
    target_node_id_2: null,
    slots,
    new_node: null,
    position: null,
    source: "text",
    rationale: "用户编辑",
  });

  // P1-5: debounce(300ms) 同步后端,失败仅日志不阻塞输入
  const scheduleRefine = (slots: Record<string, unknown>) => {
    if (!sessionId) return;
    if (refineTimerRef.current) clearTimeout(refineTimerRef.current);
    refineTimerRef.current = setTimeout(() => {
      refineTimerRef.current = null;
      api
        .refine(sessionId, makePatch(slots))
        .then((result) => {
          if (result.graph) setGraph(result.graph);
        })
        .catch((e) => console.error("[NodeInspector] refine failed:", e));
    }, 300);
  };

  const onTitleChange = (v: string) => {
    setTitleDraft(v);
    scheduleRefine({ title: v });
  };

  const onDescChange = (v: string) => {
    setDescDraft(v);
    scheduleRefine({ description: v });
  };

  // PR-ROUND-3/E-09: onBlur 时立即 refine (取消 debounce),失败回滚本地草稿并提示
  const commitRefine = async (slots: Record<string, unknown>) => {
    if (!sessionId) return;
    // 取消挂起的 debounce,避免重复请求
    if (refineTimerRef.current) {
      clearTimeout(refineTimerRef.current);
      refineTimerRef.current = null;
    }
    setSaving(true);
    setSaveError("");
    try {
      const result = await api.refine(sessionId, makePatch(slots));
      if (result.graph) setGraph(result.graph);
    } catch (e) {
      console.error("[NodeInspector] refine failed:", e);
      setSaveError("保存失败");
      // 回滚本地草稿到 graph 中的原始值
      if (graph && selectedId) {
        const n = graph.nodes[selectedId];
        if (n) {
          if ("title" in slots) setTitleDraft(n.title);
          if ("description" in slots) setDescDraft(n.description);
        }
      }
      // 3 秒后自动清除错误提示
      setTimeout(() => setSaveError(""), 3000);
    } finally {
      setSaving(false);
    }
  };

  const slotEntries = Object.entries(node.slots || {});

  return (
    <div style={{ padding: "var(--gs-space-4)" }}>
      {/* 图层 + 状态徽章 */}
      <div style={{ display: "flex", gap: "var(--gs-space-2)", marginBottom: "var(--gs-space-3)" }}>
        <span className="gs-badge" style={{ background: `${layerCfg.color}1a`, color: layerCfg.color }}>
          {layerCfg.text}
        </span>
        <span className="gs-badge" style={{ background: statusCfg.bg, color: statusCfg.color }}>
          {statusCfg.text}
        </span>
      </div>

      {/* 节点 ID */}
      <div style={{
        fontSize: "var(--gs-text-xs)", color: "var(--gs-text-3)",
        fontFamily: "var(--gs-font-mono)", marginBottom: "var(--gs-space-2)",
      }}>
        {node.id}
      </div>

      {/* 标题编辑 */}
      <div style={{ marginBottom: "var(--gs-space-3)" }}>
        <label style={{
          display: "block", fontSize: "var(--gs-text-xs)", color: "var(--gs-text-3)",
          marginBottom: "var(--gs-space-1)", fontWeight: 500,
        }}>标题</label>
        <input
          type="text"
          value={titleDraft}
          onChange={(e) => onTitleChange(e.target.value)}
          onBlur={() => commitRefine({ title: titleDraft })}
          className="gs-input"
        />
        {/* PR-ROUND-3/E-09: 保存错误/保存中状态反馈 */}
        {saveError && (
          <div style={{
            fontSize: "var(--gs-text-xs)", color: "var(--gs-danger)",
            marginTop: "var(--gs-space-1)",
          }}>{saveError} (已回滚)</div>
        )}
        {saving && (
          <div style={{
            fontSize: "var(--gs-text-xs)", color: "var(--gs-text-3)",
            marginTop: "var(--gs-space-1)",
          }}>保存中...</div>
        )}
      </div>

      {/* 描述编辑 */}
      <div style={{ marginBottom: "var(--gs-space-3)" }}>
        <label style={{
          display: "block", fontSize: "var(--gs-text-xs)", color: "var(--gs-text-3)",
          marginBottom: "var(--gs-space-1)", fontWeight: 500,
        }}>描述</label>
        <textarea
          value={descDraft}
          onChange={(e) => onDescChange(e.target.value)}
          onBlur={() => commitRefine({ description: descDraft })}
          className="gs-input"
          style={{ resize: "vertical", minHeight: 60, fontFamily: "var(--gs-font)" }}
          rows={3}
        />
      </div>

      {/* 槽位列表 */}
      <div>
        <label style={{
          display: "block", fontSize: "var(--gs-text-xs)", color: "var(--gs-text-3)",
          marginBottom: "var(--gs-space-2)", fontWeight: 500,
        }}>
          槽位 ({slotEntries.length})
        </label>
        {slotEntries.length === 0 ? (
          <div style={{
            padding: "var(--gs-space-3)", borderRadius: "var(--gs-radius)",
            background: "var(--gs-surface-2)", color: "var(--gs-text-3)",
            fontSize: "var(--gs-text-xs)", textAlign: "center",
          }}>
            暂无槽位
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--gs-space-2)" }}>
            {slotEntries.map(([key, value]) => (
              <div key={key} style={{
                display: "flex", alignItems: "center", gap: "var(--gs-space-2)",
                padding: "var(--gs-space-2) var(--gs-space-3)",
                background: "var(--gs-surface)", border: "1px solid var(--gs-border)",
                borderRadius: "var(--gs-radius-sm)",
              }}>
                <code style={{
                  fontSize: "var(--gs-text-xs)", color: "var(--gs-brand)",
                  fontFamily: "var(--gs-font-mono)", minWidth: 80, flexShrink: 0,
                }}>{key}</code>
                <span style={{
                  fontSize: "var(--gs-text-xs)", color: "var(--gs-text-2)",
                  fontFamily: "var(--gs-font-mono)",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>
                  {String(value)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
