import { useEffect, useRef, useState } from "react";
import type { ClaudeModel } from "../lib/api";

/** Lets the person choose which Claude model answers (and does the work for) their questions. */
export function ModelPicker({
  models, value, onChange, disabled,
}: { models: ClaudeModel[]; value: string; onChange: (id: string) => void; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const current = models.find((m) => m.id === value) ?? models[0];

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", esc); };
  }, [open]);

  if (!current || models.length === 0) return null;
  return (
    <div ref={box} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Choose which Claude model does the work"
        className="flex h-8 items-center gap-1.5 rounded-full border border-slate-700 bg-white px-3 text-xs font-medium text-slate-300 hover:bg-slate-950 disabled:opacity-50"
      >
        <span className="text-slate-500">Model</span>
        <span className="text-slate-100">{current.label.replace(/^Claude\s+/i, "")}</span>
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden><path d="M2 3.5l3 3 3-3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
      </button>
      {open && (
        <ul
          role="listbox"
          className="absolute bottom-10 left-0 z-20 w-72 overflow-hidden rounded-xl border border-slate-800 bg-white py-1 shadow-lg"
        >
          {models.map((m) => (
            <li key={m.id} role="option" aria-selected={m.id === current.id}>
              <button
                type="button"
                onClick={() => { onChange(m.id); setOpen(false); }}
                className={`flex w-full items-start gap-2 px-3 py-2 text-left hover:bg-slate-950 ${m.id === current.id ? "bg-sky-950" : ""}`}
              >
                <span className="mt-0.5 w-3 text-sky-600">{m.id === current.id ? "✓" : ""}</span>
                <span>
                  <span className="block text-sm font-medium text-slate-100">{m.label}</span>
                  {m.note && <span className="block text-xs text-slate-500">{m.note}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
