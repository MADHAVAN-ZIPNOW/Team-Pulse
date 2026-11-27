import { useCallback, useEffect, useRef, useState } from "react";

/** Live dictation: every finished sentence is handed to `onFinal`, the sentence being spoken to `onInterim`.
 * It keeps listening until stopped (the browser ends a session after a pause, so it is restarted automatically).
 * This uses the browser's own speech service (Chrome / Edge) and hears only this computer's microphone. */

export type MicLang = "en-IN" | "ta-IN";

interface Alt { transcript: string }
interface Result { 0: Alt; isFinal: boolean; length: number }
interface Ev { resultIndex: number; results: ArrayLike<Result> }
interface Rec {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((e: Ev) => void) | null; onerror: ((e: { error: string }) => void) | null; onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
}
type Ctor = new () => Rec;

const ctor = (): Ctor | null => {
  const w = window as unknown as { SpeechRecognition?: Ctor; webkitSpeechRecognition?: Ctor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
};

const ERRORS: Record<string, string> = {
  "not-allowed": "Microphone access is blocked. Allow the microphone for this site in your browser, then try again.",
  "service-not-allowed": "Microphone access is blocked. Allow the microphone for this site in your browser, then try again.",
  "audio-capture": "No microphone was found.",
  "network": "Speech recognition needs an internet connection.",
  "language-not-supported": "This browser cannot understand that language by voice.",
};

export function useLiveMic(lang: MicLang, onFinal: (text: string) => void) {
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState<string | null>(null);
  const want = useRef(false);
  const rec = useRef<Rec | null>(null);
  const finalRef = useRef(onFinal);
  finalRef.current = onFinal;
  const supported = typeof window !== "undefined" && ctor() !== null;

  const begin = useCallback(() => {
    const C = ctor();
    if (!C) return;
    const r = new C();
    r.lang = lang;
    r.continuous = true;
    r.interimResults = true;
    r.onresult = (e) => {
      let partial = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const text = e.results[i][0].transcript.trim();
        if (e.results[i].isFinal) { if (text) finalRef.current(text); } else partial += text + " ";
      }
      setInterim(partial.trim());
    };
    r.onerror = (e) => {
      if (e.error === "no-speech" || e.error === "aborted") return; // normal pauses
      want.current = false;
      setError(ERRORS[e.error] ?? "Listening stopped. Try again.");
    };
    r.onend = () => {
      setInterim("");
      if (want.current) { try { begin(); } catch { want.current = false; setListening(false); } } else setListening(false);
    };
    rec.current = r;
    r.start();
  }, [lang]);

  const start = useCallback(() => {
    setError(null);
    want.current = true;
    setListening(true);
    rec.current?.abort();
    begin();
  }, [begin]);

  const stop = useCallback(() => {
    want.current = false;
    rec.current?.stop();
    setListening(false);
    setInterim("");
  }, []);

  useEffect(() => () => { want.current = false; rec.current?.abort(); }, []);
  useEffect(() => { if (want.current) { rec.current?.abort(); } }, [lang]);  // a language change restarts listening in the new language

  return { supported, listening, interim, error, start, stop };
}
