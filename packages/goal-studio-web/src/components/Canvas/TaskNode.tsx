/**
 * TaskNode - 自定义任务节点 (支持三级 LOD 语义缩放)
 *
 * LOD 1 (概览): 紧凑圆点 + 标题,适合全局鸟瞰
 * LOD 2 (标准): 左侧色条 + 标题 + 状态 + 描述,适合常规编辑
 * LOD 3 (精化): LOD 2 + 槽位完整展示,适合精细调整
 *
 * R4-19: LOD 从 canvasStore 的 zoom 派生,避免每次 zoom 变化重建 node.data
 */
import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useCanvasStore } from "../../stores/canvasStore";

export type LODLevel = 1 | 2 | 3;

/** R4-19: 根据 zoom 计算 LOD 级别 (从 store 派生,不再依赖 node.data) */
export function zoomToLod(zoom: number): LODLevel {
  if (zoom < 0.5) return 1;
  if (zoom < 1.2) return 2;
  return 3;
}

interface TaskNodeData {
  label: string;
  description?: string;
  layer: number;
  status: string;
  slots: Record<string, unknown>;
  /** R4-19: lod 已移至 store 派生,保留为可选用于向后兼容 */
  lod?: LODLevel;
}

const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  draft: { color: "var(--gs-node-draft)", label: "草稿" },
  ready: { color: "var(--gs-node-active)", label: "就绪" },
  running: { color: "var(--gs-node-active)", label: "执行中" },
  done: { color: "var(--gs-node-done)", label: "完成" },
  error: { color: "var(--gs-node-error)", label: "错误" },
  blocked: { color: "var(--gs-node-pending)", label: "阻塞" },
  active: { color: "var(--gs-node-active)", label: "进行中" },
  pending: { color: "var(--gs-node-pending)", label: "待处理" },
};

const LAYER_COLOR: Record<number, string> = {
  1: "var(--gs-layer-1)",
  2: "var(--gs-layer-2)",
  3: "var(--gs-layer-3)",
};

const LAYER_BG: Record<number, string> = {
  1: "var(--gs-brand-soft)",
  2: "#f5f3ff",
  3: "#faf5ff",
};

/** LOD 1: 概览模式 — 紧凑圆点 + 标题 */
function NodeLOD1({ data, layerColor }: { data: TaskNodeData; layerColor: string }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 6,
      padding: "4px 10px",
      background: "var(--gs-surface)",
      border: `1.5px solid ${layerColor}`,
      borderRadius: 999,
      boxShadow: "var(--gs-shadow-xs)",
      fontSize: 10,
      color: "var(--gs-text-2)",
      fontWeight: 500,
      maxWidth: 120,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: "50%",
        background: layerColor, flexShrink: 0,
      }} />
      <span style={{
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {data.label}
      </span>
    </div>
  );
}

/** LOD 2: 标准模式 — 左侧色条 + 标题 + 状态 + 描述 */
function NodeLOD2({
  data, layerColor, statusCfg,
}: {
  data: TaskNodeData; layerColor: string;
  statusCfg: { color: string; label: string };
}) {
  return (
    <div style={{ display: "flex", alignItems: "stretch", width: "100%" }}>
      <div style={{ width: 4, background: layerColor, flexShrink: 0 }} />
      <div style={{ flex: 1, padding: "10px 12px 8px", minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
          <span style={{ fontSize: "var(--gs-text-xs)", color: "var(--gs-text-3)", fontWeight: 500 }}>
            L{data.layer}
          </span>
          <span style={{
            display: "inline-flex", alignItems: "center", gap: 4,
            fontSize: "var(--gs-text-xs)", color: statusCfg.color, fontWeight: 500,
          }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: statusCfg.color }} />
            {statusCfg.label}
          </span>
        </div>
        <div style={{
          fontSize: "var(--gs-text-sm)", fontWeight: 600, color: "var(--gs-text)",
          lineHeight: 1.3, marginBottom: 2,
        }}>
          {data.label}
        </div>
        {data.description && (
          <div style={{
            fontSize: "var(--gs-text-xs)", color: "var(--gs-text-3)",
            lineHeight: 1.4, overflow: "hidden", textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}>
            {data.description}
          </div>
        )}
      </div>
    </div>
  );
}

/** LOD 3: 精化模式 — LOD2 + 槽位完整展示 */
function NodeLOD3({
  data, layerColor, statusCfg,
}: {
  data: TaskNodeData; layerColor: string;
  statusCfg: { color: string; label: string };
}) {
  const slotEntries = Object.entries(data.slots || {});
  return (
    <div style={{ width: "100%" }}>
      <NodeLOD2 data={data} layerColor={layerColor} statusCfg={statusCfg} />
      {slotEntries.length > 0 && (
        <div style={{
          padding: "8px 12px 10px 16px",
          borderTop: "1px solid var(--gs-border)",
          background: LAYER_BG[data.layer] || "var(--gs-surface-2)",
        }}>
          <div style={{
            fontSize: 10, color: "var(--gs-text-3)", fontWeight: 500,
            marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5,
          }}>
            槽位 ({slotEntries.length})
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {slotEntries.map(([k, v]) => (
              <div key={k} style={{
                display: "flex", alignItems: "flex-start", gap: 6,
                fontSize: "var(--gs-text-xs)",
              }}>
                <code style={{
                  color: "var(--gs-brand)", fontFamily: "var(--gs-font-mono)",
                  minWidth: 70, flexShrink: 0, fontWeight: 500,
                }}>{k}</code>
                <span style={{
                  color: "var(--gs-text-2)", fontFamily: "var(--gs-font-mono)",
                  wordBreak: "break-all", flex: 1,
                }}>
                  {typeof v === "object" ? JSON.stringify(v) : String(v)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function TaskNodeComponent({ data, selected }: NodeProps) {
  const d = data as unknown as TaskNodeData;
  // R4-19: LOD 从 canvasStore 的 zoom 派生,避免 node.data 重建
  const zoom = useCanvasStore((s) => s.zoom);
  const lod: LODLevel = d.lod ?? zoomToLod(zoom);
  const statusCfg = STATUS_CONFIG[d.status] || STATUS_CONFIG.draft;
  const layerColor = LAYER_COLOR[d.layer] || LAYER_COLOR[1];

  // LOD 1: 紧凑模式,只显示 source handle(避免 handle 占空间)
  if (lod === 1) {
    return (
      <div style={{ position: "relative" }}>
        {d.layer > 1 && (
          <Handle type="target" position={Position.Top} style={{ display: "none" }} />
        )}
        <NodeLOD1 data={d} layerColor={layerColor} />
        <Handle type="source" position={Position.Bottom} style={{ display: "none" }} />
      </div>
    );
  }

  // LOD 2/3: 标准卡片
  const nodeWidth = lod === 3 ? 240 : 200;
  return (
    <div style={{
      background: "var(--gs-surface)",
      border: `1.5px solid ${selected ? "var(--gs-brand)" : "var(--gs-border)"}`,
      borderRadius: "var(--gs-radius-md)",
      boxShadow: selected ? "var(--gs-shadow-md)" : "var(--gs-shadow-sm)",
      width: nodeWidth,
      overflow: "hidden",
      transition: "border-color var(--gs-transition), box-shadow var(--gs-transition)",
    }}>
      {d.layer > 1 && (
        <Handle type="target" position={Position.Top} style={{ background: layerColor, width: 8, height: 8, border: "none" }} />
      )}
      {lod === 3 ? (
        <NodeLOD3 data={d} layerColor={layerColor} statusCfg={statusCfg} />
      ) : (
        <NodeLOD2 data={d} layerColor={layerColor} statusCfg={statusCfg} />
      )}
      <Handle type="source" position={Position.Bottom} style={{ background: layerColor, width: 8, height: 8, border: "none" }} />
    </div>
  );
}

export const TaskNode = memo(TaskNodeComponent);
