/**
 * Intent Store - Jotai 原子化意图解析状态
 * 管理 IntentParser 五层中间态
 *
 * Phase 3 扩展:
 *  - incrementalIntent: 增量解析预览(interim chunk 触发的低置信度结果)
 *  - pushVoiceSignal: 推入语音 chunk 到信号缓冲,供 Layer 2 时间窗口对齐
 *  - clearIncremental: 最终 chunk 确认后清空预览
 */
import { useCallback } from "react";
import { atom, useAtom } from "jotai";
// R4-18: ParsedIntent 已提升至 types/index.ts
import type { ParsedIntent } from "../types";
// 向后兼容: 重新导出 ParsedIntent,避免现有导入路径断裂
export type { ParsedIntent };

/** 信号缓冲条目 */
export interface SignalEntry {
  voice_chunk: string;
  mouse_pos: { x: number; y: number } | null;
  selection: string | null;
  timestamp: number;
  /** ASR 置信度 */
  confidence: number;
  /** 是否为最终确认 chunk */
  is_final: boolean;
}

// 原子定义
export const voiceStreamAtom = atom<string>("");
export const interimStreamAtom = atom<string>("");
export const isListeningAtom = atom<boolean>(false);
export const signalBufferAtom = atom<SignalEntry[]>([]);
export const parsedIntentAtom = atom<ParsedIntent>({
  intent: null,
  target_ref: null,
  slots: {},
  ambiguity_flag: false,
  clarification_options: [],
});
/** Phase 3: 增量解析预览(interim chunk 触发,低置信度) */
export const incrementalIntentAtom = atom<ParsedIntent | null>(null);
export const asrConfidenceAtom = atom<number>(0);

/** Hook: 使用意图解析状态 */
export function useIntentParser() {
  const [voiceStream, setVoiceStream] = useAtom(voiceStreamAtom);
  const [interimStream, setInterimStream] = useAtom(interimStreamAtom);
  const [isListening, setIsListening] = useAtom(isListeningAtom);
  const [signals, setSignals] = useAtom(signalBufferAtom);
  const [parsed, setParsed] = useAtom(parsedIntentAtom);
  const [incremental, setIncremental] = useAtom(incrementalIntentAtom);
  const [confidence, setConfidence] = useAtom(asrConfidenceAtom);

  // R4-07: pushVoiceSignal 用 useCallback 包裹,确保 deps 正确
  /** 推入语音 chunk 到信号缓冲 */
  const pushVoiceSignal = useCallback(
    (entry: Omit<SignalEntry, "timestamp">) => {
      // P1-6: 滑动窗口,仅保留最近 200 条信号避免内存无限增长
      setSignals((prev) => [...prev.slice(-200), { ...entry, timestamp: Date.now() }]);
      setConfidence(entry.confidence);
      if (entry.is_final) {
        setVoiceStream((prev) => prev + entry.voice_chunk);
        setInterimStream("");
      } else {
        setInterimStream(entry.voice_chunk);
      }
    },
    [setSignals, setConfidence, setVoiceStream, setInterimStream],
  );

  // R4-07: clearIncremental 用 useCallback 包裹
  /** 清空增量预览(最终 chunk 确认后调用) */
  const clearIncremental = useCallback(() => {
    setIncremental(null);
    setInterimStream("");
  }, [setIncremental, setInterimStream]);

  // R4-07: resetVoice 用 useCallback 包裹
  /** 重置全部语音相关状态 */
  const resetVoice = useCallback(() => {
    setVoiceStream("");
    setInterimStream("");
    setSignals([]);
    setIncremental(null);
    setConfidence(0);
  }, [setVoiceStream, setInterimStream, setSignals, setIncremental, setConfidence]);

  return {
    voiceStream,
    interimStream,
    isListening,
    signals,
    parsed,
    incremental,
    confidence,
    setVoiceStream,
    setIsListening,
    setSignals,
    setParsed,
    setIncremental,
    setConfidence,
    pushVoiceSignal,
    clearIncremental,
    resetVoice,
  };
}
