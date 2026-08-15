/**
 * C6: useSession — connectWs 抽取后 createAndDraft/loadSession 行为一致
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

const wsInstances: Array<{
  sid: string;
  connect: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  onStatusChange?: (s: string) => void;
}> = [];

vi.mock("../src/api/wsClient", () => ({
  WSClient: class {
    onStatusChange?: (s: string) => void;
    connect = vi.fn();
    close = vi.fn();
    intentParse = vi.fn();
    voiceChunk = vi.fn();
    mouseSelect = vi.fn();
    mouseHover = vi.fn();
    constructor(public sid: string, public handler: unknown) {
      wsInstances.push(this as never);
    }
  },
}));

vi.mock("../src/api/apiClient", () => ({
  api: {
    createSession: vi.fn(async () => ({ id: "abc123def456", name: "t" })),
    draft: vi.fn(async () => ({ nodes: {}, edges: [], root_id: null, version: 1 })),
    getSession: vi.fn(async (id: string) => ({ id, phase: "visualize" })),
    // G3/G4: 撤销 / 导出 / 安装
    undo: vi.fn(async () => ({
      graph: { nodes: {}, edges: [], root_id: null, version: 2 },
      snapshots_remaining: 0,
      phase: "refine",
    })),
    exportArtifact: vi.fn(async () => ({ location: "/tmp/exports/x", phase: "done" })),
    installArtifact: vi.fn(async () => ({ result: "installed", phase: "done" })),
  },
}));

import { useSession } from "../src/hooks/useSession";

describe("useSession connectWs", () => {
  beforeEach(() => {
    wsInstances.length = 0;
  });

  it("createAndDraft 建立 WS 连接", async () => {
    const { result } = renderHook(() => useSession());
    await act(async () => {
      await result.current.createAndDraft({ goal: "测试" } as never);
    });
    expect(wsInstances).toHaveLength(1);
    expect(wsInstances[0].sid).toBe("abc123def456");
    expect(wsInstances[0].connect).toHaveBeenCalledTimes(1);
  });

  it("loadSession 复用同一连接逻辑并关闭旧连接", async () => {
    const { result } = renderHook(() => useSession());
    await act(async () => {
      await result.current.createAndDraft({ goal: "测试" } as never);
    });
    await act(async () => {
      await result.current.loadSession("abc123def456");
    });
    expect(wsInstances).toHaveLength(2);
    expect(wsInstances[0].close).toHaveBeenCalledTimes(1);
    expect(wsInstances[1].connect).toHaveBeenCalledTimes(1);
  });

  it("undo (G3): 调用 REST 并按剩余快照数更新 canUndo", async () => {
    const { result } = renderHook(() => useSession());
    await act(async () => {
      await result.current.createAndDraft({ goal: "测试" } as never);
    });
    // canUndo 初始 false(loadSession 重置)
    expect(result.current.canUndo).toBe(false);
    let ok = false;
    await act(async () => {
      ok = await result.current.undo();
    });
    expect(ok).toBe(true);
    expect(result.current.canUndo).toBe(false); // snapshots_remaining=0
    const { api } = await import("../src/api/apiClient");
    expect(api.undo).toHaveBeenCalledWith("abc123def456");
  });
});
