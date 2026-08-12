import { describe, it, expect } from "vitest";
import { NodeReferenceResolver, routeModality } from "../src/components/IntentParser/IntentParser";
import { NodeGraph, NodeLayer, NodeStatus } from "../src/types";

describe("NodeReferenceResolver", () => {
  const makeGraph = (): NodeGraph => ({
    nodes: {
      "n1": {
        id: "n1", layer: NodeLayer.L1_PLANNING, title: "需求分析",
        description: "解析目标", node_type: "task", status: NodeStatus.DRAFT,
        slots: {}, parent_id: null, children_ids: [], position: [0, 0], metadata: {},
      },
      "n2": {
        id: "n2", layer: NodeLayer.L1_PLANNING, title: "技能编写",
        description: "起草 SKILL.md", node_type: "task", status: NodeStatus.DRAFT,
        slots: {}, parent_id: null, children_ids: [], position: [100, 0], metadata: {},
      },
    },
    edges: [], root_id: "n1", version: 1,
  });

  it("resolves explicit id", () => {
    const r = new NodeReferenceResolver(makeGraph(), [], "n1", null, null);
    const result = r.resolve("n1");
    expect(result.ok).toBe(true);
    expect(result.nodeId).toBe("n1");
  });

  it("resolves by title keyword", () => {
    const r = new NodeReferenceResolver(makeGraph(), [], null, null, null);
    const result = r.resolve("需求");
    expect(result.ok).toBe(true);
    expect(result.nodeId).toBe("n1");
  });

  it("uses selection for pronoun", () => {
    const r = new NodeReferenceResolver(makeGraph(), [], "n2", null, null);
    const result = r.resolve("这个");
    expect(result.ok).toBe(true);
    expect(result.nodeId).toBe("n2");
  });
});

describe("routeModality", () => {
  it("routes explicit id to TEXT", () => {
    expect(routeModality("node-1", false, false, false)).toBe("TEXT");
  });

  it("routes pronoun with selection to SELECTION_PRIMARY", () => {
    expect(routeModality("这个", true, false, false)).toBe("SELECTION_PRIMARY");
  });

  it("routes pronoun without context to CLARIFY", () => {
    expect(routeModality("这个", false, false, false)).toBe("CLARIFY");
  });

  it("routes mobile to VOICE_NUMBER_OVERLAY", () => {
    expect(routeModality("这个", false, false, false, true)).toBe("VOICE_NUMBER_OVERLAY");
  });
});
