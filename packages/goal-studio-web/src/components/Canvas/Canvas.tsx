/**
 * Canvas - 三层可视化画布 (语义缩放 LOD)
 *
 * LOD 分级 (基于 viewport.zoom):
 *   zoom < 0.5  → LOD 1 (概览): 只显示 L1 节点,紧凑圆点+标题
 *   0.5 ≤ zoom < 1.2 → LOD 2 (标准): 显示 L1+L2,标准卡片
 *   zoom ≥ 1.2  → LOD 3 (精化): 显示全部 L1+L2+L3,详细槽位
 *
 * 父子关系: 通过 parent_id 绘制虚线连接(非执行顺序边)
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  type Node,
  type Edge,
  type NodeChange,
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCanvasStore } from "../../stores/canvasStore";
import { TaskNode, zoomToLod, type LODLevel } from "./TaskNode";

const nodeTypes = { task: TaskNode };

const LAYER_COLOR: Record<number, string> = {
  1: "#6366f1",
  2: "#8b5cf6",
  3: "#a855f7",
};

/** LOD 决定显示哪些层级 */
function lodToMaxLayer(lod: LODLevel): number {
  return lod; // LOD 1 → 只 L1; LOD 2 → L1+L2; LOD 3 → 全部
}

export function Canvas() {
  const graph = useCanvasStore((s) => s.graph);
  const selectNode = useCanvasStore((s) => s.selectNode);
  const hoverNode = useCanvasStore((s) => s.hoverNode);
  const setZoom = useCanvasStore((s) => s.setZoom);
  const setViewport = useCanvasStore((s) => s.setViewport);
  const [currentZoom, setCurrentZoom] = useState(1);

  const lod = zoomToLod(currentZoom);
  const maxLayer = lodToMaxLayer(lod);

  // R4-02/R4-03: 受控节点状态,通过 applyNodeChanges 更新(支持拖拽+选中高亮)
  const [rfNodes, setRfNodes] = useState<Node[]>([]);

  // graph 或 maxLayer 变化时同步节点(从 store 派生)
  // R4-19: lod 从 node.data 中移出,由 TaskNode 通过 store 订阅,避免 zoom 变化重建 data
  useEffect(() => {
    if (!graph) {
      setRfNodes([]);
      return;
    }
    setRfNodes(
      Object.values(graph.nodes)
        .filter((n) => n.layer <= maxLayer)
        .map((n) => ({
          id: n.id,
          type: "task",
          position: { x: n.position[0], y: n.position[1] },
          data: {
            label: n.title,
            description: n.description,
            layer: n.layer,
            status: n.status,
            slots: n.slots,
          },
        })),
    );
  }, [graph, maxLayer]);

  const rfEdges: Edge[] = useMemo(() => {
    if (!graph) return [];
    const visibleNodeIds = new Set(
      Object.values(graph.nodes)
        .filter((n) => n.layer <= maxLayer)
        .map((n) => n.id)
    );

    // 同层执行顺序边 (实线)
    const execEdges: Edge[] = graph.edges
      .filter((e) => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target))
      .map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.label || undefined,
        labelStyle: { fontSize: 11, fill: "#a1a1aa", fontWeight: 500 },
        labelBgStyle: { fill: "#ffffff" },
        labelBgPadding: [4, 2],
        type: "smoothstep",
        animated: e.edge_type === "control",
        style: { stroke: LAYER_COLOR[1], strokeWidth: 1.5 },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 16, height: 16,
          color: LAYER_COLOR[1],
        },
      }));

    // 父子关系边 (虚线,L2→L1 或 L3→L2)
    const parentEdges: Edge[] = [];
    for (const node of Object.values(graph.nodes)) {
      if (node.layer > maxLayer) continue;
      if (!node.parent_id) continue;
      if (!visibleNodeIds.has(node.parent_id)) continue;
      parentEdges.push({
        id: `pe-${node.parent_id}-${node.id}`,
        source: node.parent_id,
        target: node.id,
        type: "straight",
        style: {
          stroke: LAYER_COLOR[node.layer] || LAYER_COLOR[1],
          strokeWidth: 1,
          strokeDasharray: "4 3",
          opacity: 0.6,
        },
      });
    }

    return [...execEdges, ...parentEdges];
  }, [graph, maxLayer]);

  // P-LOW-2-8: layerCounts useMemo 避免每次 render 重算
  const layerCounts = useMemo(() => {
    if (!graph) return { l1: 0, l2: 0, l3: 0 };
    const nodes = Object.values(graph.nodes);
    return {
      l1: nodes.filter((n) => n.layer === 1).length,
      l2: nodes.filter((n) => n.layer === 2).length,
      l3: nodes.filter((n) => n.layer === 3).length,
    };
  }, [graph]);

  // R4-20: viewportRef 存最新 viewport,rAF 回调从 ref 读取
  const viewportRef = useRef({ zoom: 1, x: 0, y: 0 });
  // P-LOW-2-8: rAF 节流 onMove,避免高频 setState(专家 4: setCurrentZoom 移入 rAF)
  const rafRef = useRef<number | null>(null);

  const onMove = useCallback(
    // ReactFlow OnMove event 可能是 MouseEvent/TouchEvent/null
    (_: unknown, vp: { zoom: number; x: number; y: number }) => {
      // R4-20: 用 ref 存最新 vp,rAF 回调从 ref 读取
      viewportRef.current = vp;
      if (rafRef.current !== null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        const v = viewportRef.current;
        setCurrentZoom(v.zoom);
        setZoom(v.zoom);
        setViewport({ x: v.x, y: v.y });
      });
    },
    [setZoom, setViewport],
  );

  // 卸载时清理 rAF
  useEffect(() => {
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, []);

  // R4-02: onNodesChange 调用 applyNodeChanges (受控模式必须应用变更,否则拖拽回弹、选中高亮失效)
  // R4-03: applyNodeChanges 会正确维护每个节点的 selected 字段,传递给 TaskNode
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setRfNodes((nds) => applyNodeChanges(changes, nds));
      changes.forEach((c) => {
        if (c.type === "select" && c.selected) {
          selectNode(c.id);
        }
      });
    },
    [selectNode]
  );

  const onNodeMouseEnter = useCallback(
    (_: unknown, node: Node) => hoverNode(node.id),
    [hoverNode]
  );

  const onNodeMouseLeave = useCallback(() => hoverNode(null), [hoverNode]);

  if (!graph) {
    return (
      <div style={{
        display: "flex", height: "100%", alignItems: "center", justifyContent: "center",
        flexDirection: "column", gap: "var(--gs-space-3)",
      }}>
        <div style={{
          width: 56, height: 56, borderRadius: "var(--gs-radius-lg)",
          background: "var(--gs-brand-soft)", display: "flex",
          alignItems: "center", justifyContent: "center",
          fontSize: 28, color: "var(--gs-brand)",
        }}>+</div>
        <div style={{ color: "var(--gs-text-3)", fontSize: "var(--gs-text-sm)" }}>
          输入目标后点击「起草 DAG」生成工作流
        </div>
      </div>
    );
  }

  return (
    <div style={{ position: "relative", width: "100%", height: "100%" }}>
      {/* LOD 指示器 (右上角) */}
      <div style={{
        position: "absolute", top: 12, right: 12, zIndex: 5,
        background: "var(--gs-surface)", border: "1px solid var(--gs-border)",
        borderRadius: "var(--gs-radius)", boxShadow: "var(--gs-shadow-sm)",
        padding: "6px 10px", fontSize: "var(--gs-text-xs)",
        color: "var(--gs-text-2)", display: "flex", gap: 10, alignItems: "center",
      }}>
        <span style={{ fontWeight: 600, color: "var(--gs-brand)" }}>
          LOD {lod}
        </span>
        <span style={{ color: "var(--gs-text-3)" }}>
          zoom {currentZoom.toFixed(2)}
        </span>
        <span style={{ color: "var(--gs-text-3)" }}>·</span>
        <span>
          L1:<strong>{layerCounts.l1}</strong>
          {layerCounts.l2 > 0 && <> L2:<strong>{layerCounts.l2}</strong></>}
          {layerCounts.l3 > 0 && <> L3:<strong>{layerCounts.l3}</strong></>}
        </span>
      </div>

      {/* LOD 缩放提示 (左下角) */}
      <div style={{
        position: "absolute", bottom: 12, left: 12, zIndex: 5,
        background: "var(--gs-surface)", border: "1px solid var(--gs-border)",
        borderRadius: "var(--gs-radius)", boxShadow: "var(--gs-shadow-sm)",
        padding: "8px 12px", fontSize: "var(--gs-text-xs)",
        color: "var(--gs-text-3)", lineHeight: 1.6,
      }}>
        <div style={{ fontWeight: 500, color: "var(--gs-text-2)", marginBottom: 4 }}>
          语义缩放
        </div>
        <div>LOD 1 &lt; 0.5: 概览 (仅 L1)</div>
        <div>LOD 2 ≥ 0.5: 标准 (L1+L2)</div>
        <div>LOD 3 ≥ 1.2: 精化 (全部)</div>
      </div>

      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeMouseEnter={onNodeMouseEnter}
        onNodeMouseLeave={onNodeMouseLeave}
        onMove={onMove}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={3}
        proOptions={{ hideAttribution: true }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1.5}
          color="var(--gs-border)"
        />
        <Controls
          showInteractive={false}
          style={{
            background: "var(--gs-surface)",
            border: "1px solid var(--gs-border)",
            borderRadius: "var(--gs-radius)",
            boxShadow: "var(--gs-shadow-sm)",
          }}
        />
      </ReactFlow>
    </div>
  );
}
