/**
 * Canvas Store - Zustand 全局画布状态
 * 管理 NodeGraph / 当前会话 / 选中节点 / 棘轮历史
 */
import { create } from "zustand";
import type {
  NodeGraph,
  Node,
  Patch,
  Report,
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

  // Actions
  setGraph: (graph: NodeGraph) => void;
  setPhase: (phase: SessionPhase) => void;
  selectNode: (nodeId: string | null) => void;
  hoverNode: (nodeId: string | null) => void;
  pushRecentNode: (nodeId: string) => void;
  applyPatch: (patch: Patch) => void;
  setZoom: (zoom: number) => void;
  setViewport: (vp: { x: number; y: number }) => void;
  recordRatchet: (record: RatchetRecord) => void;
  updateBestScore: (score: number) => void;
}

export const useCanvasStore = create<CanvasState>((set, get) => ({
  graph: null,
  phase: "init",
  selectedNodeId: null,
  hoverNodeId: null,
  recentNodeStack: [],
  ratchetHistory: [],
  bestScore: 0,
  zoom: 1,
  viewport: { x: 0, y: 0 },

  setGraph: (graph) => set({ graph }),
  setPhase: (phase) => set({ phase }),
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
  applyPatch: (patch) =>
    set((s) => {
      if (!s.graph) return s;
      // MVP: 简化补丁应用,实际由后端 Adapter.refine 处理
      const newGraph: NodeGraph = {
        ...s.graph,
        version: s.graph.version + 1,
        nodes: { ...s.graph.nodes },
        edges: [...s.graph.edges],
      };
      if (
        patch.op === "update_node" &&
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
      return { graph: newGraph };
    }),
  setZoom: (zoom) => set({ zoom }),
  setViewport: (viewport) => set({ viewport }),
  recordRatchet: (record) =>
    set((s) => ({ ratchetHistory: [...s.ratchetHistory, record] })),
  updateBestScore: (score) =>
    set((s) => (score > s.bestScore ? { bestScore: score } : s)),
}));
