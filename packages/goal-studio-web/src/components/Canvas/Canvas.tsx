/**
 * Canvas - 三层可视化画布核心组件
 * 基于 React Flow,支持语义缩放(LOD)
 *
 * 三层:
 *   L1_PLANNING - 任务规划层
 *   L2_ALLOCATION - Agent 分配层
 *   L3_REFINEMENT - 节点精化层
 *
 * 语义缩放:
 *   zoom < 0.5  → minimalNode (只显示标题)
 *   zoom 0.5-2  → standardNode (标题+描述+状态)
 *   zoom > 2.0  → detailNode (完整槽位编辑)
 */
import { useCallback, useMemo } from "react";
import {
  ReactFlow,
  type Node,
  type Edge,
  type NodeChange,
  type EdgeChange,
  applyNodeChanges,
  applyEdgeChanges,
  useViewport,
  Background,
  Controls,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCanvasStore } from "../../stores/canvasStore";
import { NodeLayer, NodeStatus } from "../../types";

/** 自定义节点类型将在 Phase 2 实现 */
const nodeTypes = {};

export function Canvas() {
  const graph = useCanvasStore((s) => s.graph);
  const selectNode = useCanvasStore((s) => s.selectNode);
  const hoverNode = useCanvasStore((s) => s.hoverNode);
  const setZoom = useCanvasStore((s) => s.setZoom);
  const setViewport = useCanvasStore((s) => s.setViewport);

  const { zoom } = useViewport();

  // 转换内部 Node 为 React Flow Node
  const rfNodes: Node[] = useMemo(() => {
    if (!graph) return [];
    return Object.values(graph.nodes).map((n) => ({
      id: n.id,
      type: "default", // Phase 2 按 zoom 切换 minimalNode/standardNode/detailNode
      position: { x: n.position[0], y: n.position[1] },
      data: {
        label: n.title,
        description: n.description,
        layer: n.layer,
        status: n.status,
        slots: n.slots,
      },
      selected: false,
    }));
  }, [graph]);

  const rfEdges: Edge[] = useMemo(() => {
    if (!graph) return [];
    return graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.label || undefined,
      animated: e.edge_type === "control",
    }));
  }, [graph]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      // MVP: 仅处理 selection
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
      <div className="flex h-full items-center justify-center text-gray-400">
        请先 /studio draft 起草 DAG
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={rfNodes}
      edges={rfEdges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onNodeMouseEnter={onNodeMouseEnter}
      onNodeMouseLeave={onNodeMouseLeave}
      onMove={(_, vp) => {
        setZoom(vp.zoom);
        setViewport({ x: vp.x, y: vp.y });
      }}
      fitView
      attributionPosition="bottom-right"
    >
      <Background />
      <Controls />
    </ReactFlow>
  );
}
