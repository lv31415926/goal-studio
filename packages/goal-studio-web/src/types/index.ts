/**
 * goal-studio 前端核心类型定义
 * 与 goal-studio-core Python 端的 contract.py / node_graph.py 镜像
 */

/** 三层画布 */
export enum NodeLayer {
  L1_PLANNING = 1,
  L2_ALLOCATION = 2,
  L3_REFINEMENT = 3,
}

/** 节点状态 */
export enum NodeStatus {
  DRAFT = "draft",
  READY = "ready",
  RUNNING = "running",
  DONE = "done",
  ERROR = "error",
  BLOCKED = "blocked",
}

/** 产物类型 */
export type ProductType = "skill" | "code" | "doc" | "workflow" | "custom";

/** 节点 */
export interface Node {
  id: string;
  layer: NodeLayer;
  title: string;
  description: string;
  node_type: "task" | "agent" | "tool" | "decision" | "io";
  status: NodeStatus;
  slots: Record<string, unknown>;
  parent_id: string | null;
  children_ids: string[];
  position: [number, number];
  metadata: Record<string, unknown>;
}

/** 边 */
export interface Edge {
  id: string;
  source: string;
  target: string;
  label: string;
  edge_type: "data" | "control" | "condition";
  metadata: Record<string, unknown>;
}

/** DAG 图 */
export interface NodeGraph {
  nodes: Record<string, Node>;
  edges: Edge[];
  root_id: string | null;
  version: number;
}

/** 目标契约四要素 */
export interface GoalContract {
  goal: string;
  prerequisites: string[];
  constraints: string[];
  success_criteria: string[];
  product_type: ProductType;
  metadata: Record<string, unknown>;
}

/** 补丁操作类型 */
export type PatchOp =
  | "add_node"
  | "update_node"
  | "remove_node"
  | "add_edge"
  | "remove_edge"
  | "reorder";

/** 精化补丁 */
export interface Patch {
  op: PatchOp;
  target_node_id: string | null;
  target_node_id_2: string | null;
  slots: Record<string, unknown>;
  new_node: Record<string, unknown> | null;
  position: number | null;
  source: "voice" | "text" | "mouse";
  rationale: string;
}

/** 验证报告 */
export interface Report {
  passed: boolean;
  score: number;
  dimensions: Record<string, number>;
  issues: string[];
  suggestions: string[];
  evaluator: string;
}

/** 产物 */
export interface Artifact {
  product_type: ProductType;
  content: string;
  files: Record<string, string>;
  metadata: Record<string, unknown>;
  graph_snapshot: Record<string, unknown>;
  version: string;
}

/** 棘轮判定结果 */
export type RatchetDecision = "keep" | "revert" | "early_stop" | "arbitrate";

/** 会话阶段 */
export type SessionPhase =
  | "init"
  | "draft"
  | "visualize"
  | "refine"
  | "verify"
  | "build"
  | "done"
  | "paused";
