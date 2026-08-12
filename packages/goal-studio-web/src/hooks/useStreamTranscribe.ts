/**
 * useStreamTranscribe - 语音流式转录 Hook
 * Phase 2 接入 Vercel AI SDK 7 的 streamTranscribe
 * MVP 阶段使用 Web Speech API 降级
 */
import { useCallback, useRef, useState } from "react";

interface UseStreamTranscribeOptions {
  onChunk?: (chunk: string, isFinal: boolean) => void;
  onError?: (error: Error) => void;
  lang?: string;
}

export function useStreamTranscribe(options: UseStreamTranscribeOptions = {}) {
  const { onChunk, onError, lang = "zh-CN" } = options;
  const [isListening, setIsListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const recognitionRef = useRef<any>(null);

  const start = useCallback(() => {
    if (isListening) return;

    // 降级: Web Speech API
    const SpeechRecognition =
      (window as any).SpeechRecognition ||
      (window as any).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      onError?.(new Error("SpeechRecognition not supported in this browser"));
      return;
    }

    const recognition = new SpeechRecognition();
    recognition.lang = lang;
    recognition.continuous = true;
    recognition.interimResults = true;

    recognition.onresult = (event: any) => {
      let interim = "";
      let final = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          final += result[0].transcript;
        } else {
          interim += result[0].transcript;
        }
      }
      if (final) {
        setTranscript((prev) => prev + final);
        onChunk?.(final, true);
      }
      if (interim) {
        onChunk?.(interim, false);
      }
    };

    recognition.onerror = (event: any) => {
      onError?.(new Error(`SpeechRecognition error: ${event.error}`));
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognition.start();
    recognitionRef.current = recognition;
    setIsListening(true);
  }, [isListening, lang, onChunk, onError]);

  const stop = useCallback(() => {
    recognitionRef.current?.stop();
    setIsListening(false);
  }, []);

  const reset = useCallback(() => {
    setTranscript("");
  }, []);

  return {
    isListening,
    transcript,
    start,
    stop,
    reset,
  };
}
