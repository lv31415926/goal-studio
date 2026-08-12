/**
 * NodeInspector - 节点详情编辑侧边栏
 * 展示选中节点的槽位,支持文本编辑
 */
import { useCanvasStore } from "../../stores/canvasStore";

export function NodeInspector() {
  const graph = useCanvasStore((s) => s.graph);
  const selectedId = useCanvasStore((s) => s.selectedNodeId);
  const applyPatch = useCanvasStore((s) => s.applyPatch);

  if (!graph || !selectedId) {
    return (
      <div className="p-4 text-gray-400 text-sm">
        选择一个节点查看详情
      </div>
    );
  }

  const node = graph.nodes[selectedId];
  if (!node) return null;

  const layerLabel = ["", "L1 规划", "L2 分配", "L3 精化"][node.layer] || "";

  return (
    <div className="p-4 space-y-3">
      <div>
        <div className="text-xs text-gray-400">{layerLabel}</div>
        <input
          type="text"
          value={node.title}
          onChange={(e) =>
            applyPatch({
              op: "update_node",
              target_node_id: selectedId,
              target_node_id_2: null,
              slots: { title: e.target.value },
              new_node: null,
              position: null,
              source: "text",
              rationale: "用户编辑标题",
            })
          }
          className="w-full px-2 py-1 border rounded text-sm"
        />
      </div>
      <div>
        <label className="text-xs text-gray-400">描述</label>
        <textarea
          value={node.description}
          onChange={(e) =>
            applyPatch({
              op: "update_node",
              target_node_id: selectedId,
              target_node_id_2: null,
              slots: { description: e.target.value },
              new_node: null,
              position: null,
              source: "text",
              rationale: "用户编辑描述",
            })
          }
          className="w-full px-2 py-1 border rounded text-sm"
          rows={3}
        />
      </div>
      <div>
        <div className="text-xs text-gray-400">槽位</div>
        <pre className="text-xs bg-gray-50 p-2 rounded overflow-auto">
          {JSON.stringify(node.slots, null, 2)}
        </pre>
      </div>
    </div>
  );
}
