import type { User } from "../lib/api";
import { Avatar } from "./common";

export function Header({ user, onLogout }: { user: User; onLogout: () => void }) {
  return (
    <header className="sticky top-0 z-10 border-b border-slate-800 bg-white">
      <div className="mx-auto flex max-w-[1800px] items-center justify-between gap-3 px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-linear-to-br from-[#2b88d8] via-[#7160e8] to-[#d46ef0] text-white" aria-hidden>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z" /></svg>
          </span>
          <h1 className="text-base font-semibold text-slate-100">Azure DevOps PR Assistant</h1>
        </div>
        <div className="flex items-center gap-3">
          <div className="hidden text-right sm:block">
            <p className="text-sm font-medium text-slate-200">{user.name}</p>
            {user.email && <p className="text-xs text-slate-500">{user.email}</p>}
          </div>
          <Avatar name={user.name} />
          <button onClick={onLogout} className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800">
            Log out
          </button>
        </div>
      </div>
    </header>
  );
}
