/**
 * Canvas Store - Zustand 全局画布状态
 * 管理 NodeGraph / 当前会话 / 选中节点 / 棘轮历史
 */
import { create } from "zustand";
import type {
  Node,
  NodeGraph,
  Edge,
  Patch,
  SessionPhase,
  RatchetDecision,
} from "../types";

interface RatchetRecord {
  round: number;
  old_score: number;
  new_score: number;
  delta: number;
  decision: RatchetDecision;
}

interface CanvasState {
  // 当前图
  graph: NodeGraph | null;
  // 当前会话阶段
  phase: SessionPhase;
  // P1-5: 当前会话 ID (供 NodeInspector 调用 api.refine 同步后端)
  sessionId: string | null;
  // 选中节点 ID
  selectedNodeId: string | null;
  // 悬停节点 ID
  hoverNodeId: string | null;
  // 最近编辑节点栈(供节点引用解析)
  recentNodeStack: string[];
  // 棘轮历史
  ratchetHistory: RatchetRecord[];
  // 当前最佳分数
  bestScore: number;
  // 缩放级别(用于语义缩放)
  zoom: number;
  // 视口平移
  viewport: { x: number; y: number };
  // R4-11: ASR 期间画布锁定标志 (禁用 NodeInspector 编辑)
  canvasLocked: boolean;

  // Actions
  setGraph: (graph: NodeGraph) => void;
  setPhase: (phase: SessionPhase) => void;
  setSessionId: (sessionId: string | null) => void;
  selectNode: (nodeId: string | null) => void;
  hoverNode: (nodeId: string | null) => void;
  pushRecentNode: (nodeId: string) => void;
  applyPatch: (patch: Patch) => void;
  setZoom: (zoom: number) => void;
  setViewport: (vp: { x: number; y: number }) => void;
  recordRatchet: (record: RatchetRecord) => void;
  updateBestScore: (score: number) => void;
  setCanvasLocked: (locked: boolean) => void;
}

export const useCanvasStore = create<CanvasState>((set, get) => ({
  graph: null,
  phase: "init",
  sessionId: null,
  selectedNodeId: null,
  hoverNodeId: null,
  recentNodeStack: [],
  ratchetHistory: [],
  bestScore: 0,
  zoom: 1,
  viewport: { x: 0, y: 0 },
  canvasLocked: false,

  setGraph: (graph) => set({ graph }),
  setPhase: (phase) => set({ phase }),
  setSessionId: (sessionId) => set({ sessionId }),
  selectNode: (nodeId) => {
    set({ selectedNodeId: nodeId });
    if (nodeId) get().pushRecentNode(nodeId);
  },
  hoverNode: (nodeId) => set({ hoverNodeId: nodeId }),
  pushRecentNode: (nodeId) =>
    set((s) => ({
      recentNodeStack: [
        nodeId,
        ...s.recentNodeStack.filter((id) => id !== nodeId),
      ].slice(0, 10),
    })),
  // R4-06: applyPatch 处理 add/remove/reorder 操作 (本地乐观更新)
  applyPatch: (patch) =>
    set((s) => {
      if (!s.graph) return s;
      const newGraph: NodeGraph = {
        ...s.graph,
        version: s.graph.version + 1,
        nodes: { ...s.graph.nodes },
        edges: [...s.graph.edges],
      };

      switch (patch.op) {
        case "update_node": {
          if (
            patch.target_node_id &&
            newGraph.nodes[patch.target_node_id]
          ) {
            const node = newGraph.nodes[patch.target_node_id];
            newGraph.nodes[patch.target_node_id] = {
              ...node,
              slots: { ...node.slots, ...patch.slots },
              title:
                typeof patch.slots["title"] === "string"
                  ? (patch.slots["title"] as string)
                  : node.title,
              description:
                typeof patch.slots["description"] === "string"
                  ? (patch.slots["description"] as string)
                  : node.description,
            };
          }
          break;
        }
        case "add_node": {
          // R4-06: 向 nodes 添加新节点
          if (patch.new_node) {
            const id =
              (patch.new_node["id"] as string) || patch.target_node_id;
            if (id) {
              newGraph.nodes[id] = patch.new_node as unknown as Node;
            }
          }
          break;
        }
        case "remove_node": {
          // R4-06: 从 nodes 移除节点 + 移除关联边
          if (patch.target_node_id) {
            const targetId = patch.target_node_id;
            delete newGraph.nodes[targetId];
            newGraph.edges = newGraph.edges.filter(
              (e) => e.source !== targetId && e.target !== targetId,
            );
          }
          break;
        }
        case "add_edge": {
          // R4-06: 向 edges 添加新边
          if (patch.target_node_id && patch.target_node_id_2) {
            const newEdge: Edge = {
              id: `e-${patch.target_node_id}-${patch.target_node_id_2}-${Date.now()}`,
              source: patch.target_node_id,
              target: patch.target_node_id_2,
              label: (patch.slots["label"] as string) || "",
              edge_type:
                (patch.slots["edge_type"] as Edge["edge_type"]) || "data",
              metadata: {},
            };
            newGraph.edges.push(newEdge);
          }
          break;
        }
        case "remove_edge": {
          // R4-06: 从 edges 移除边
          if (patch.target_node_id && patch.target_node_id_2) {
            const source = patch.target_node_id;
            const target = patch.target_node_id_2;
            newGraph.edges = newGraph.edges.filter(
              (e) => !(e.source === source && e.target === target),
            );
          }
          break;
        }
        case "reorder": {
          // R4-06: 调整节点顺序 (在 parent 的 children_ids 中重新排序)
          if (patch.target_node_id && patch.position !== null) {
            const node = newGraph.nodes[patch.target_node_id];
            if (node && node.parent_id && newGraph.nodes[node.parent_id]) {
              const parent = newGraph.nodes[node.parent_id];
              const children = parent.children_ids.filter(
                (id) => id !== patch.target_node_id,
              );
              const insertAt = Math.min(
                Math.max(0, patch.position),
                children.length,
              );
              children.splice(insertAt, 0, patch.target_node_id);
              newGraph.nodes[node.parent_id] = {
                ...parent,
                children_ids: children,
              };
            }
          }
          break;
        }
      }
      return { graph: newGraph };
    }),
  setZoom: (zoom) => set({ zoom }),
  setViewport: (viewport) => set({ viewport }),
  recordRatchet: (record) =>
    set((s) => ({ ratchetHistory: [...s.ratchetHistory, record] })),
  updateBestScore: (score) =>
    set((s) => (score > s.bestScore ? { bestScore: score } : s)),
  setCanvasLocked: (locked) => set({ canvasLocked: locked }),
}));
