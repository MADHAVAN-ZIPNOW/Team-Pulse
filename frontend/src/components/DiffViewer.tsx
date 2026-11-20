import { useMemo } from "react";
import hljs from "highlight.js/lib/common";
import type { FileChange } from "../lib/api";
import { languageFor } from "../lib/format";

function highlight(code: string, lang: string | undefined): string {
  if (!lang || !hljs.getLanguage(lang)) return escapeHtml(code);
  try {
    return hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
  } catch {
    return escapeHtml(code);
  }
}

const escapeHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

export function DiffViewer({ file }: { file: FileChange }) {
  const lang = languageFor(file.path);
  const rows = useMemo(() => {
    if (!file.diff) return [];
    return file.diff.split("\n").map((line) => {
      const isHeader = line.startsWith("---") || line.startsWith("+++");
      const kind = isHeader ? "meta" : line.startsWith("@@") ? "hunk" : line[0] === "+" ? "add" : line[0] === "-" ? "del" : "ctx";
      const body = kind === "add" || kind === "del" || kind === "ctx" ? line.slice(1) : line;
      return { kind, sign: kind === "add" ? "+" : kind === "del" ? "-" : " ", html: kind === "meta" || kind === "hunk" ? escapeHtml(line) : highlight(body, lang) };
    });
  }, [file.diff, lang]);

  if (!file.diff) {
    return <p className="p-4 text-sm text-slate-500">{file.diffSkippedReason ?? "No textual changes to show."}</p>;
  }
  const styles: Record<string, string> = {
    add: "bg-emerald-950/50", del: "bg-red-950/50", hunk: "bg-sky-950/40 text-sky-300", meta: "text-slate-500", ctx: "",
  };
  return (
    <div className="overflow-x-auto rounded border border-slate-800 bg-slate-950 text-xs leading-5">
      <table className="w-full border-collapse font-mono">
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={styles[r.kind]}>
              <td className={`w-6 select-none px-2 text-right ${r.kind === "add" ? "text-emerald-400" : r.kind === "del" ? "text-red-400" : "text-slate-600"}`}>
                {r.kind === "meta" || r.kind === "hunk" ? "" : r.sign}
              </td>
              <td className="whitespace-pre pr-4" dangerouslySetInnerHTML={{ __html: r.html || "&nbsp;" }} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
