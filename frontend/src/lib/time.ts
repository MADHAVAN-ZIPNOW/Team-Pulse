// Time helpers for the Trends page. The backend sends UTC ISO strings; everything is shown in the viewer's local time.

export function formatDuration(fromIso: string, toIso: string | null, nowMs = Date.now()): string {
  const ms = Math.max(0, (toIso ? Date.parse(toIso) : nowMs) - Date.parse(fromIso));
  const mins = Math.floor(ms / 60000);
  if (mins < 1) return "under a minute";
  if (mins < 60) return `${mins} min`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return mins % 60 ? `${hours} h ${mins % 60} min` : `${hours} h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} d ${hours % 24} h` : `${days} d`;
}

/** "14:30" today, "Mon 14:30" this week, "12 Mar, 14:30" this year, "12 Mar 2024, 14:30" otherwise. */
export function formatWhen(iso: string | null, nowMs = Date.now()): string {
  if (!iso) return "never";
  const d = new Date(iso);
  const now = new Date(nowMs);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return time;
  const days = (now.getTime() - d.getTime()) / 86400000;
  if (days >= 0 && days < 6) return `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
  const opts: Intl.DateTimeFormatOptions = d.getFullYear() === now.getFullYear()
    ? { day: "numeric", month: "short" }
    : { day: "numeric", month: "short", year: "numeric" };
  return `${d.toLocaleDateString([], opts)}, ${time}`;
}

export function regionName(code: string): string {
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}
