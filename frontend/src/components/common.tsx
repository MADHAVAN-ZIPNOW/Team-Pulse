import type { ReactNode } from "react";
import { initials } from "../lib/format";

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-400" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-sky-400" />
      {label}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded border border-red-900/60 bg-red-950 p-3 text-sm text-red-200">
      <p>{message}</p>
      {onRetry && (
        <button onClick={onRetry} className="mt-2 rounded-md border border-red-700 px-2.5 py-1 text-xs font-medium hover:bg-red-900/50">
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded border border-dashed border-slate-800 p-4 text-center text-sm text-slate-500">{children}</p>;
}

export function Card({ title, children, actions }: { title?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="min-w-0 rounded-lg border border-slate-800 bg-white shadow-sm">
      {title && (
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
          {actions}
        </div>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Avatar({ name }: { name: string }) {
  return (
    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sky-900 text-xs font-semibold text-sky-100" aria-hidden>
      {initials(name)}
    </span>
  );
}

export function Selector({
  label, value, onChange, options, disabled, loading, error, onRetry, placeholder,
}: {
  label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[];
  disabled?: boolean; loading?: boolean; error?: string | null; onRetry?: () => void; placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">{label}</span>
      <select
        value={value}
        disabled={disabled || loading}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-sky-500 disabled:opacity-50"
      >
        <option value="">{loading ? "Loading…" : placeholder ?? `Select ${label.toLowerCase()}`}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {error && <div className="mt-1.5"><ErrorState message={error} onRetry={onRetry} /></div>}
    </label>
  );
}

export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "green" | "red" | "amber" | "sky" }) {
  const tones = {
    slate: "bg-slate-800 text-slate-300", green: "bg-emerald-950 text-emerald-300",
    red: "bg-red-950 text-red-300", amber: "bg-amber-950 text-amber-300", sky: "bg-sky-950 text-sky-300",
  };
  return <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${tones[tone]}`}>{children}</span>;
}
