import { useCallback, useEffect, useRef, useState } from "react";

/** Voice for the chat: speak your question (speech to text) and have answers read aloud (text to speech).
 * Both use the browser's built-in speech, so nothing is sent to our server or to any extra service. */

export type VoiceLang = "en-IN" | "ta-IN";
export const VOICE_LANGS: { id: VoiceLang; label: string }[] = [
  { id: "en-IN", label: "English" },
  { id: "ta-IN", label: "தமிழ்" },
];

interface RecognitionResult { 0: { transcript: string }; isFinal: boolean; length: number }
interface RecognitionEvent { resultIndex: number; results: ArrayLike<RecognitionResult> }
interface Recognition {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
}
type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const MIC_ERRORS: Record<string, string> = {
  "not-allowed": "Microphone access is blocked. Allow the microphone for this site in your browser, then try again.",
  "service-not-allowed": "Microphone access is blocked. Allow the microphone for this site in your browser, then try again.",
  "no-speech": "I did not hear anything. Tap the microphone and speak again.",
  "audio-capture": "No microphone was found.",
  "network": "Speech recognition needs an internet connection.",
  "language-not-supported": "This browser cannot understand that language by voice.",
};

/** Turns chat Markdown into plain sentences for the speech voice (no symbols read out, no code blocks). */
export function plainTextForSpeech(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, " (code omitted) ")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s*/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+\.\s+/gm, "")
    .replace(/[*_>|~]/g, "")
    .replace(/\s*\n\s*/g, ". ")
    .replace(/\.{2,}/g, ".")
    .trim();
}

/** Dictation. `onText` gets the full text so far, live while speaking. `base` is what was already typed. */
export function useSpeechInput(lang: VoiceLang, onText: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<Recognition | null>(null);
  const supported = typeof window !== "undefined" && recognitionCtor() !== null;

  const stop = useCallback(() => rec.current?.stop(), []);

  const start = useCallback((base: string) => {
    const Ctor = recognitionCtor();
    if (!Ctor) return;
    setError(null);
    rec.current?.abort();
    const r = new Ctor();
    r.lang = lang;
    r.continuous = true;
    r.interimResults = true;
    const prefix = base && !/\s$/.test(base) ? base + " " : base;
    r.onresult = (e) => {
      let text = "";
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
      onText(prefix + text);
    };
    r.onerror = (e) => {
      if (e.error !== "aborted") setError(MIC_ERRORS[e.error] ?? "Voice input stopped. Try again.");
    };
    r.onend = () => setListening(false);
    rec.current = r;
    try {
      r.start();
      setListening(true);
    } catch {
      setError("Voice input could not start. Try again.");
    }
  }, [lang, onText]);

  useEffect(() => () => rec.current?.abort(), []);
  return { supported, listening, error, start, stop, clearError: () => setError(null) };
}

/** Read an answer aloud. Only one thing is spoken at a time. */
export function useSpeechOutput(lang: VoiceLang) {
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;

  const stop = useCallback(() => {
    if (supported) window.speechSynthesis.cancel();
    setSpeakingId(null);
  }, [supported]);

  const speak = useCallback((id: string, markdown: string) => {
    if (!supported) return;
    window.speechSynthesis.cancel();
    const text = plainTextForSpeech(markdown);
    if (!text) return;
    // Long answers are read in sentence-sized pieces: some browsers cut off one very long utterance.
    const pieces = text.match(/[^.!?]+[.!?]*\s*/g) ?? [text];
    const voices = window.speechSynthesis.getVoices();
    const voice = voices.find((v) => v.lang === lang) ?? voices.find((v) => v.lang.startsWith(lang.slice(0, 2)));
    pieces.forEach((piece, i) => {
      const u = new SpeechSynthesisUtterance(piece.trim());
      u.lang = lang;
      if (voice) u.voice = voice;
      if (i === pieces.length - 1) {
        u.onend = () => setSpeakingId(null);
        u.onerror = () => setSpeakingId(null);
      }
      window.speechSynthesis.speak(u);
    });
    setSpeakingId(id);
  }, [lang, supported]);

  useEffect(() => () => { if (supported) window.speechSynthesis.cancel(); }, [supported]);
  return { supported, speakingId, speak, stop };
}
