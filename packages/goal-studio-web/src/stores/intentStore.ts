/**
 * Intent Store - Jotai 原子化意图解析状态
 * 管理 IntentParser 五层中间态
 */
import { atom, useAtom } from "jotai";

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
}

/** 信号缓冲条目 */
export interface SignalEntry {
  voice_chunk: string;
  mouse_pos: { x: number; y: number } | null;
  selection: string | null;
  timestamp: number;
}

// 原子定义
export const voiceStreamAtom = atom<string>("");
export const isListeningAtom = atom<boolean>(false);
export const signalBufferAtom = atom<SignalEntry[]>([]);
export const parsedIntentAtom = atom<ParsedIntent>({
  intent: null,
  target_ref: null,
  slots: {},
  ambiguity_flag: false,
  clarification_options: [],
});
export const asrConfidenceAtom = atom<number>(0);

/** Hook: 使用意图解析状态 */
export function useIntentParser() {
  const [voiceStream, setVoiceStream] = useAtom(voiceStreamAtom);
  const [isListening, setIsListening] = useAtom(isListeningAtom);
  const [signals, setSignals] = useAtom(signalBufferAtom);
  const [parsed, setParsed] = useAtom(parsedIntentAtom);
  const [confidence, setConfidence] = useAtom(asrConfidenceAtom);

  return {
    voiceStream,
    isListening,
    signals,
    parsed,
    confidence,
    setVoiceStream,
    setIsListening,
    setSignals,
    setParsed,
    setConfidence,
  };
}
