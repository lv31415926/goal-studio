/**
 * useIncrementalParse - Phase 3 语音增量解析调度
 *
 * 职责:
 *  - interim chunk: 节流(默认 250ms)后通过 WS voice_chunk 发送,触发后端 parse_incremental
 *  - final chunk:   立即通过 WS voice_chunk(is_final=true)发送,触发后端完整解析
 *  - 同步推入 intentStore.signalBuffer 供 Layer 2 时间窗口对齐
 *
 * 配合 useStreamTranscribe 的 onChunk(chunk, isFinal, confidence) 使用。
 */
import { useCallback, useRef } from "react";
import { useIntentParser } from "../stores/intentStore";

interface UseIncrementalParseOptions {
  /** 通过 WS 发送 voice_chunk (由 useSession.sendVoiceChunk 提供) */
  sendVoiceChunk: ((text: string, confidence: number, isFinal: boolean) => void) | null;
  /** interim chunk 节流间隔 (ms),默认 250 */
  throttleMs?: number;
}

export function useIncrementalParse(options: UseIncrementalParseOptions) {
  const { sendVoiceChunk, throttleMs = 250 } = options;
  const { pushVoiceSignal, clearIncremental } = useIntentParser();

  const lastSendRef = useRef(0);
  const pendingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 缓存最近 interim 文本,供节流定时器发送最新值
  const latestInterimRef = useRef<{ text: string; confidence: number } | null>(null);

  const flushInterim = useCallback(() => {
    pendingTimerRef.current = null;
    const latest = latestInterimRef.current;
    if (!latest || !latest.text) return;
    sendVoiceChunk?.(latest.text, latest.confidence, false);
    lastSendRef.current = Date.now();
  }, [sendVoiceChunk]);

  /** 处理来自 useStreamTranscribe 的 chunk */
  const handleChunk = useCallback(
    (chunk: string, isFinal: boolean, confidence: number) => {
      // 1. 始终推入信号缓冲 (Layer 2 时间窗口对齐)
      pushVoiceSignal({
        voice_chunk: chunk,
        mouse_pos: null,
        selection: null,
        confidence,
        is_final: isFinal,
      });

      if (!sendVoiceChunk) return;

      if (isFinal) {
        // final chunk: 立即发送,取消挂起的 interim 节流
        if (pendingTimerRef.current) {
          clearTimeout(pendingTimerRef.current);
          pendingTimerRef.current = null;
        }
        latestInterimRef.current = null;
        sendVoiceChunk(chunk, confidence, true);
        lastSendRef.current = Date.now();
        return;
      }

      // interim chunk: 节流
      latestInterimRef.current = { text: chunk, confidence };
      const now = Date.now();
      const elapsed = now - lastSendRef.current;
      if (elapsed >= throttleMs) {
        flushInterim();
      } else if (!pendingTimerRef.current) {
        pendingTimerRef.current = setTimeout(flushInterim, throttleMs - elapsed);
      }
    },
    [sendVoiceChunk, throttleMs, pushVoiceSignal, flushInterim],
  );

  /** 停止语音时清理挂起的节流定时器 */
  const dispose = useCallback(() => {
    if (pendingTimerRef.current) {
      clearTimeout(pendingTimerRef.current);
      pendingTimerRef.current = null;
    }
    latestInterimRef.current = null;
    // P1-11: 重置 lastSendRef,避免重启后首条 chunk 因 elapsed 误判被跳过
    lastSendRef.current = 0;
  }, []);

  return { handleChunk, dispose, clearIncremental };
}
