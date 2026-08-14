/**
 * IntentParser - 五层意图解析 (前端入口)
 *
 * Layer 1: ModalityIngestion  - 多模态信号采集
 * Layer 2: SignalBuffer        - 时间窗口对齐 (200ms, 2s 滑动)
 * Layer 3: IncrementalLLMParser - LLM 增量解析输出 JSON
 * Layer 4: NodeReferenceResolver - 三信号融合算法消解节点引用
 * Layer 5: ActionExecutor      - DAG mutation + undo/redo
 *
 * Phase 1 实现 Layer 4 (节点引用解析),Layer 3 走后端 LLM
 */

import type { Node, NodeGraph } from "../../types";

/** 节点引用解析器 */
export class NodeReferenceResolver {
  private graph: NodeGraph;
  private recentStack: string[];
  private selection: string | null;
  private hover: string | null;

  // 权重配置(可调)
  private readonly weights = {
    explicit: 1.0, // 显式 ID/name 匹配
    selection: 0.4, // 当前选中
    hover: 0.35, // 鼠标悬停
    recency: 0.15, // 最近编辑栈
    semantic: 0.25, // 语义相似度
    spatial: 0.3, // 空间语言
    property: 0.3, // 属性描述
  };

  private readonly ambiguityThreshold = 0.2;

  constructor(
    graph: NodeGraph,
    recentStack: string[],
    selection: string | null,
    hover: string | null
  ) {
    this.graph = graph;
    this.recentStack = recentStack;
    this.selection = selection;
    this.hover = hover;
  }

  /** 主解析入口 */
  resolve(refExpr: string, asrConfidence: number = 0.9): ResolveResult {
    const candidates = this.generateCandidates(refExpr);
    if (candidates.length === 0) {
      return {
        ok: false,
        reason: "找不到匹配节点,请明确指定",
      };
    }

    const scored = candidates.map((node) => ({
      node,
      score: this.scoreNode(node, refExpr) * asrConfidence,
    }));
    scored.sort((a, b) => b.score - a.score);

    if (scored.length === 1) {
      return { ok: true, nodeId: scored[0].node.id };
    }

    const top1 = scored[0];
    const top2 = scored[1];
    if (top1.score - top2.score < this.ambiguityThreshold) {
      return {
        ok: false,
        reason: "你指的是哪个?",
        clarificationOptions: scored.slice(0, 5).map((s) => s.node.id),
      };
    }
    return { ok: true, nodeId: top1.node.id };
  }

  private generateCandidates(refExpr: string): Node[] {
    const allNodes = Object.values(this.graph.nodes);
    const expr = refExpr.trim().toLowerCase();

    // 显式 ID 匹配
    const byId = allNodes.find((n) => n.id.toLowerCase() === expr);
    if (byId) return [byId];

    // 显式 name 匹配
    const byName = allNodes.filter(
      (n) => n.title.toLowerCase().includes(expr) && expr.length > 0
    );
    if (byName.length > 0) return byName;

    // 代词 → 全节点候选(靠打分排序)
    if (["这个", "那个", "this", "that", "it"].includes(expr)) {
      return allNodes;
    }

    // 默认全节点候选
    return allNodes;
  }

  private scoreNode(node: Node, refExpr: string): number {
    let score = 0;
    const expr = refExpr.trim().toLowerCase();

    // 信号 A: 显式匹配
    if (
      node.id.toLowerCase() === expr ||
      node.title.toLowerCase().includes(expr)
    ) {
      score += this.weights.explicit;
    }

    // 信号 B: 当前选中
    if (node.id === this.selection) {
      score += this.weights.selection;
    }

    // 信号 C: 鼠标悬停
    if (node.id === this.hover) {
      score += this.weights.hover;
    }

    // 信号 D: 最近编辑栈(越近权重越高)
    const recencyIdx = this.recentStack.indexOf(node.id);
    if (recencyIdx >= 0) {
      score += this.weights.recency * (1 - recencyIdx / this.recentStack.length);
    }

    // 信号 E: 语义相似度(MVP: 简单关键词匹配,Phase 2 接 embedding)
    if (expr && expr !== "这个" && expr !== "那个") {
      const titleSim = this.simpleSimilarity(expr, node.title.toLowerCase());
      const descSim = this.simpleSimilarity(expr, node.description.toLowerCase());
      score += this.weights.semantic * Math.max(titleSim, descSim);
    }

    return Math.min(score, 1.0);
  }

  private simpleSimilarity(a: string, b: string): number {
    if (!a || !b) return 0;
    const setA = new Set(a.split(/\s+/));
    const setB = new Set(b.split(/\s+/));
    const intersection = new Set([...setA].filter((x) => setB.has(x)));
    const union = new Set([...setA, ...setB]);
    return union.size > 0 ? intersection.size / union.size : 0;
  }
}

export interface ResolveResult {
  ok: boolean;
  nodeId?: string;
  reason?: string;
  clarificationOptions?: string[];
}

/** 模态路由决策树 */
export function routeModality(
  refExpr: string,
  hasSelection: boolean,
  hasHover: boolean,
  mouseRecent: boolean,
  isMobile: boolean = false
): ModalityRoute {
  const expr = refExpr.trim().toLowerCase();

  if (isMobile) return "VOICE_NUMBER_OVERLAY";

  if (/^n(ode)?[-_]?\d+/i.test(expr)) return "TEXT";
  if (/(左边|右边|上面|下面|left|right|above|below)/i.test(expr)) {
    return mouseRecent ? "MOUSE_PRIMARY_VOICE_CONFIRM" : "VOICE_CANVAS_HIGHLIGHT";
  }
  if (/(这个|那个|this|that|it)/i.test(expr)) {
    if (hasSelection) return "SELECTION_PRIMARY";
    if (hasHover) return "HOVER_PRIMARY";
    return "CLARIFY";
  }
  if (/(红色|高优先级|red|high.priority)/i.test(expr)) {
    return "VOICE_VISUAL_FILTER";
  }
  return "VOICE_PRIMARY";
}

export type ModalityRoute =
  | "TEXT"
  | "MOUSE_PRIMARY_VOICE_CONFIRM"
  | "VOICE_CANVAS_HIGHLIGHT"
  | "SELECTION_PRIMARY"
  | "HOVER_PRIMARY"
  | "CLARIFY"
  | "VOICE_VISUAL_FILTER"
  | "VOICE_PRIMARY"
  | "VOICE_NUMBER_OVERLAY";
