/**
 * useStreamTranscribe - 语音流式转录 Hook (Phase 3)
 *
 * Provider 抽象:
 *  - "webspeech" (默认): 浏览器 Web Speech API,零依赖降级方案
 *  - "vercel-ai": Vercel AI SDK 7 streamTranscribe (需后端 /api/transcribe 流式端点)
 *
 * 暴露:
 *  - transcript: 累积的最终转录文本
 *  - interimTranscript: 当前 interim (未确认) 文本,用于实时预览
 *  - confidence: 最近一次 chunk 的 ASR 置信度
 *  - onChunk(chunk, isFinal, confidence): chunk 回调,供消费方推入信号缓冲/WS
 */
import { useCallback, useEffect, useRef, useState } from "react";

// Web Speech API 最小类型定义(浏览器原生 API, TS 标准库未收录)
interface SpeechRecognitionAlternative {
  transcript: string;
  confidence: number;
}
interface SpeechRecognitionResult {
  isFinal: boolean;
  length: number;
  [index: number]: SpeechRecognitionAlternative;
}
interface SpeechRecognitionResultList {
  length: number;
  [index: number]: SpeechRecognitionResult;
}
interface SpeechRecognitionEvent {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}
interface SpeechRecognitionErrorEvent {
  error: string;
}
interface SpeechRecognitionInstance {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
}
interface SpeechRecognitionConstructor {
  new (): SpeechRecognitionInstance;
}

export type TranscribeProvider = "webspeech" | "vercel-ai";

interface UseStreamTranscribeOptions {
  onChunk?: (chunk: string, isFinal: boolean, confidence: number) => void;
  onError?: (error: Error) => void;
  lang?: string;
  /** 转录提供方,默认 webspeech */
  provider?: TranscribeProvider;
  /** vercel-ai 提供方的流式端点 URL (provider="vercel-ai" 时必填) */
  transcribeEndpoint?: string;
  /** vercel-ai 提供方需要 session id 用于关联 */
  sessionId?: string;
}

/**
 * Vercel AI SDK 7 streamTranscribe 客户端封装
 * 通过 fetch + ReadableStream 消费 NDJSON 流:
 *   {"text":"你","isFinal":false,"confidence":0.4}
 *   {"text":"你好","isFinal":false,"confidence":0.6}
 *   {"text":"你好","isFinal":true,"confidence":0.95}
 */
async function streamFromVercelAI(
  endpoint: string,
  sessionId: string | undefined,
  lang: string,
  onChunk: (chunk: string, isFinal: boolean, confidence: number) => void,
  onError: (e: Error) => void,
  signal: AbortSignal,
): Promise<void> {
  try {
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, lang }),
      signal,
    });
    if (!resp.ok || !resp.body) {
      throw new Error(`transcribe endpoint ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const evt = JSON.parse(trimmed);
          onChunk(
            evt.text ?? "",
            evt.isFinal ?? false,
            typeof evt.confidence === "number" ? evt.confidence : 0.9,
          );
        } catch {
          // 忽略不完整 JSON
        }
      }
    }
  } catch (e) {
    if ((e as Error).name !== "AbortError") {
      onError(e as Error);
    }
  }
}

/** P0-4: continuous 模式自动重启次数上限,超过则上报错误并停止 */
const MAX_RESTART_ATTEMPTS = 10;

export function useStreamTranscribe(options: UseStreamTranscribeOptions = {}) {
  const {
    onChunk,
    onError,
    lang = "zh-CN",
    provider = "webspeech",
    transcribeEndpoint,
    sessionId,
  } = options;

  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [interimTranscript, setInterimTranscript] = useState("");
  const [confidence, setConfidence] = useState(0);

  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // stoppedRef: 标记是否为主动停止,阻止 webspeech onend 自动重启
  const stoppedRef = useRef(false);
  // P0-4: continuous 模式自动重启次数,配合退避策略避免重启风暴
  const restartAttemptsRef = useRef(0);
  // P1-9: 用 ref 镜像 isListening,避免 start() 闭包捕获陈旧 state 导致重复创建 recognition
  const isListeningRef = useRef(false);
  // 用 ref 持有最新回调,避免 start() 闭包捕获旧值
  const onChunkRef = useRef(onChunk);
  const onErrorRef = useRef(onError);
  // R4-21: 自动重启 setTimeout 引用,unmount 时清除避免泄漏
  const restartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // R4-22: ref 赋值移入 useEffect,避免每次 render 直接写 ref
  useEffect(() => {
    onChunkRef.current = onChunk;
    onErrorRef.current = onError;
  });

  const emitChunk = useCallback(
    (chunk: string, isFinal: boolean, conf: number) => {
      setConfidence(conf);
      if (isFinal) {
        setTranscript((prev) => prev + chunk);
        setInterimTranscript("");
      } else {
        setInterimTranscript(chunk);
      }
      onChunkRef.current?.(chunk, isFinal, conf);
    },
    [],
  );

  const start = useCallback(() => {
    // P1-9: 用 ref 判断,避免快速双击因 isListening state 陈旧而创建两个 recognition
    if (isListeningRef.current) return;

    if (provider === "vercel-ai") {
      if (!transcribeEndpoint) {
        onErrorRef.current?.(new Error("vercel-ai provider requires transcribeEndpoint"));
        return;
      }
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      stoppedRef.current = false;
      isListeningRef.current = true;
      setIsListening(true);
      void streamFromVercelAI(
        transcribeEndpoint,
        sessionId,
        lang,
        emitChunk,
        (e) => onErrorRef.current?.(e),
        ctrl.signal,
      ).finally(() => {
        if (!stoppedRef.current) {
          isListeningRef.current = false;
          setIsListening(false);
        }
      });
      return;
    }

    // 默认: Web Speech API
    const SpeechRecognition =
      (window as unknown as Record<string, SpeechRecognitionConstructor | undefined>).SpeechRecognition ||
      (window as unknown as Record<string, SpeechRecognitionConstructor | undefined>).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      onErrorRef.current?.(new Error("SpeechRecognition not supported in this browser"));
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = lang;
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      // R4-21: 收到结果时重置重启计数,表示识别正常运行
      restartAttemptsRef.current = 0;
      let interim = "";
      let final = "";
      let finalConf = 0.9;
      let interimConf = 0.5;
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        const alt: SpeechRecognitionAlternative = result[0] ?? { transcript: "", confidence: 0.9 };
        const conf = typeof alt.confidence === "number" ? alt.confidence : 0.9;
        if (result.isFinal) {
          final += alt.transcript ?? "";
          finalConf = conf;
        } else {
          interim += alt.transcript ?? "";
          interimConf = conf;
        }
      }
      if (final) emitChunk(final, true, finalConf);
      if (interim) emitChunk(interim, false, interimConf);
    };

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      onErrorRef.current?.(new Error(`SpeechRecognition error: ${event.error}`));
    };

    recognition.onend = () => {
      // continuous 模式下 onend 可能因网络抖动触发,自动重启 (P0-4: 限次数+退避)
      if (!stoppedRef.current && recognitionRef.current === recognition) {
        if (restartAttemptsRef.current < MAX_RESTART_ATTEMPTS) {
          restartAttemptsRef.current += 1;
          const delay = Math.min(1000 * restartAttemptsRef.current, 5000);
          // R4-21: setTimeout 保存引用到 ref,unmount 时可清除
          restartTimerRef.current = setTimeout(() => {
            restartTimerRef.current = null;
            // 退避期间可能已被 stop,二次确认避免重复创建
            if (stoppedRef.current || recognitionRef.current !== recognition) return;
            try {
              recognition.start();
            } catch {
              isListeningRef.current = false;
              setIsListening(false);
            }
          }, delay);
        } else {
          // P0-4: 超过重启上限,上报错误并停止
          onErrorRef.current?.(new Error("语音识别重启次数过多"));
          stoppedRef.current = true;
          recognitionRef.current = null;
          isListeningRef.current = false;
          setIsListening(false);
        }
      } else {
        isListeningRef.current = false;
        setIsListening(false);
      }
    };

    try {
      stoppedRef.current = false;
      // P0-4: 用户主动 start 时重置重启计数
      restartAttemptsRef.current = 0;
      recognition.start();
      recognitionRef.current = recognition;
      isListeningRef.current = true;
      setIsListening(true);
    } catch (e) {
      onErrorRef.current?.(e as Error);
    }
  }, [lang, provider, transcribeEndpoint, sessionId, emitChunk]);

  const stop = useCallback(() => {
    stoppedRef.current = true;
    if (provider === "vercel-ai") {
      abortRef.current?.abort();
      abortRef.current = null;
    } else {
      recognitionRef.current?.stop();
      recognitionRef.current = null;
    }
    isListeningRef.current = false;
    setIsListening(false);
  }, [provider]);

  const reset = useCallback(() => {
    setTranscript("");
    setInterimTranscript("");
    setConfidence(0);
  }, []);

  // P-HIGH-5: unmount 清理 — 避免 recognition/AbortController 泄漏
  useEffect(() => {
    return () => {
      // R4-21: 清除自动重启定时器
      if (restartTimerRef.current) {
        clearTimeout(restartTimerRef.current);
        restartTimerRef.current = null;
      }
      if (recognitionRef.current) {
        try {
          recognitionRef.current.onend = null;
          recognitionRef.current.onerror = null;
          recognitionRef.current.onresult = null;
          recognitionRef.current.stop();
        } catch {
          /* ignore */
        }
        recognitionRef.current = null;
      }
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
      isListeningRef.current = false;
    };
  }, []);

  return {
    isListening,
    transcript,
    interimTranscript,
    confidence,
    start,
    stop,
    reset,
  };
}
