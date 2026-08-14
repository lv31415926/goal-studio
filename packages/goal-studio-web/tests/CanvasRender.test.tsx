import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";

// jsdom 无 ResizeObserver, ReactFlow 依赖它 — 提供 mock
class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}
globalThis.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;
import { ReactFlowProvider } from "@xyflow/react";
import { Canvas } from "../src/components/Canvas/Canvas";
import { useCanvasStore } from "../src/stores/canvasStore";
import type { NodeGraph } from "../src/types";
import quantGraph from "./fixtures-quant-graph.json";

describe("Canvas 渲染真实量化案例图", () => {
  it("加载 42 节点量化图后应渲染出 L1 节点", () => {
    // 注入真实量化案例图到 store
    useCanvasStore.getState().setGraph(quantGraph as unknown as NodeGraph);

    const { container } = render(
      <ReactFlowProvider>
        <Canvas />
      </ReactFlowProvider>,
    );

    // ReactFlow 渲染的节点容器
    const rfNodes = container.querySelectorAll(".react-flow__node");
    expect(rfNodes.length).toBeGreaterThan(0);

    // LOD 指示器应显示节点统计
    expect(container.textContent).toContain("L1:");
  });

  it("graph 为 null 时显示空态提示", () => {
    useCanvasStore.getState().setGraph(null as unknown as NodeGraph);
    const { container } = render(
      <ReactFlowProvider>
        <Canvas />
      </ReactFlowProvider>,
    );
    expect(container.textContent).toContain("起草 DAG");
  });
});
