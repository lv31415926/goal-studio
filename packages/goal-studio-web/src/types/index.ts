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

/** 棘轮响应 — refine 接口实际返回结构 (含 decision/score/graph) */
export interface RatchetResponse {
  decision: string;
  old_score: number;
  new_score: number;
  delta: number;
  best_score: number;
  current_score: number;
  reverted: boolean;
  early_stopped: boolean;
  message: string;
  graph: NodeGraph | null;
}

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

/** 会话摘要 (列表视图,与后端 /sessions 响应一致) */
export interface SessionSummary {
  id: string;
  name: string;
  phase: string;
  best_score: number;
  updated_at: string;
}

/** 适配器信息 */
export interface AdapterInfo {
  product_type: string;
  name: string;
  description: string;
}

// ============================================================================
// R4-18: ParsedIntent 提升至 types (从 intentStore.ts 迁移)
// ============================================================================

/** 解析中的意图(JSON 中间态) */
export interface ParsedIntent {
  intent: "create" | "modify" | "delete" | "connect" | "query" | null;
  target_ref: {
    type: "id" | "name" | "spatial" | "property" | "pronoun";
    value: string;
    confidence: number;
  } | null;
  slots: Record<string, unknown>;
  ambiguity_flag: boolean;
  clarification_options: string[];
  /** Phase 3: 解析来源/置信度,与后端 ParsedIntent.parse_source 对齐 */
  parse_source?: string;
  parse_confidence?: number;
  raw_text?: string;
  /** R4-09: 是否为增量解析结果 (后端 is_interim 字段) */
  is_interim?: boolean;
}

// ============================================================================
// E-03: WebSocket 消息判别联合类型
// 与 goal-studio-server ws_protocol.py 对齐,每个 type 对应一个 payload 结构
// ============================================================================

/** WS 消息基础结构 */
export interface WSMessageBase {
  session_id: string | null;
  timestamp: number;
}

/** graph_update: 图变更通知 */
export interface GraphUpdateMessage extends WSMessageBase {
  type: "graph_update";
  payload: { graph: NodeGraph };
}

/** phase_change: 阶段变更通知 */
export interface PhaseChangeMessage extends WSMessageBase {
  type: "phase_change";
  payload: { phase: SessionPhase };
}

/** intent_parsed: 意图解析结果 */
export interface IntentParsedMessage extends WSMessageBase {
  type: "intent_parsed";
  payload: { intent: Record<string, unknown> | null; version?: number };
}

/** node_resolved: 节点引用消解结果 */
export interface NodeResolvedMessage extends WSMessageBase {
  type: "node_resolved";
  payload: { resolve: Record<string, unknown> | null };
}

/** action_executed: 指令执行结果 */
export interface ActionExecutedMessage extends WSMessageBase {
  type: "action_executed";
  payload: {
    graph: NodeGraph | null;
    execution: Record<string, unknown>;
  };
}

/** R4-18: clarification_needed — 需要用户澄清的消息 */
export interface ClarificationNeededMessage extends WSMessageBase {
  type: "clarification_needed";
  payload: {
    message: string;
    options: string[];
  };
}

/** error: 服务端错误 */
export interface ErrorMessage extends WSMessageBase {
  type: "error";
  payload: { error: string; code?: string };
}

/** pong: 心跳响应 */
export interface PongMessage extends WSMessageBase {
  type: "pong";
  payload: Record<string, unknown>;
}

/** WS 服务端消息判别联合 - 通过 type 字段区分 payload 结构 */
export type ServerMessage =
  | GraphUpdateMessage
  | PhaseChangeMessage
  | IntentParsedMessage
  | NodeResolvedMessage
  | ActionExecutedMessage
  | ClarificationNeededMessage
  | ErrorMessage
  | PongMessage;

/** 类型守卫: 判断消息是否为指定 type */
export function isWSMessage<T extends ServerMessage["type"]>(
  msg: { type: string; payload: Record<string, unknown> },
  type: T,
): msg is Extract<ServerMessage, { type: T }> {
  return msg.type === type;
}
