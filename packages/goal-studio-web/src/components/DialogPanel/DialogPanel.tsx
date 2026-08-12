/**
 * DialogPanel - 对话/语音交互面板
 * 支持文本输入 + 语音输入(push-to-talk)
 */
import { useState, useCallback } from "react";
import { useStreamTranscribe } from "../../hooks/useStreamTranscribe";
import { useIntentParser } from "../../stores/intentStore";
import { useCanvasStore } from "../../stores/canvasStore";

export function DialogPanel() {
  const [input, setInput] = useState("");
  const { isListening, transcript, start, stop, reset } = useStreamTranscribe({
    onChunk: (chunk, isFinal) => {
      if (isFinal) {
        setInput((prev) => prev + chunk);
      }
    },
  });
  const { setVoiceStream, setIsListening } = useIntentParser();
  const applyPatch = useCanvasStore((s) => s.applyPatch);

  const handleSend = useCallback(() => {
    if (!input.trim()) return;
    // MVP: 直接把文本作为 voice_stream,Phase 2 走 LLM 解析
    setVoiceStream(input);
    setIsListening(false);
    setInput("");
    reset();
    // 实际补丁生成由后端 LLM 完成,前端只展示
  }, [input, setVoiceStream, setIsListening, reset]);

  const handleVoiceToggle = useCallback(() => {
    if (isListening) {
      stop();
      setIsListening(false);
    } else {
      start();
      setIsListening(true);
    }
  }, [isListening, start, stop, setIsListening]);

  return (
    <div className="flex flex-col h-full border-t">
      <div className="flex-1 overflow-auto p-3 text-sm">
        {transcript && (
          <div className="text-gray-500 italic mb-2">[语音] {transcript}</div>
        )}
      </div>
      <div className="flex gap-2 p-3 border-t">
        <button
          onClick={handleVoiceToggle}
          className={`px-3 py-2 rounded ${
            isListening ? "bg-red-500 text-white" : "bg-gray-200"
          }`}
          aria-label={isListening ? "停止语音输入" : "开始语音输入"}
        >
          {isListening ? "⏹" : "🎤"}
        </button>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSend()}
          placeholder="输入或说出修改指令..."
          className="flex-1 px-3 py-2 border rounded"
        />
        <button
          onClick={handleSend}
          className="px-4 py-2 bg-blue-500 text-white rounded"
        >
          发送
        </button>
      </div>
    </div>
  );
}
