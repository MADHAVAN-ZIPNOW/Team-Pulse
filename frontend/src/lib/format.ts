export const branchName = (ref: string) => ref.replace(/^refs\/heads\//, "");

export function formatDate(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "" : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export const initials = (name: string) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("") || "?";

const EXT_LANG: Record<string, string> = {
  ts: "typescript", tsx: "typescript", js: "javascript", jsx: "javascript", mjs: "javascript", py: "python",
  cs: "csharp", java: "java", go: "go", rs: "rust", rb: "ruby", php: "php", json: "json", yml: "yaml",
  yaml: "yaml", xml: "xml", csproj: "xml", html: "xml", css: "css", scss: "scss", sql: "sql", sh: "bash",
  md: "markdown", cpp: "cpp", c: "c", h: "c", kt: "kotlin", swift: "swift", ps1: "powershell",
};

export function languageFor(path: string): string | undefined {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return EXT_LANG[ext];
}
