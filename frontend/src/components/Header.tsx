import type { User } from "../lib/api";
import { Avatar } from "./common";

export function Header({ user, onLogout }: { user: User; onLogout: () => void }) {
  return (
    <header className="sticky top-0 z-10 border-b border-slate-800 bg-white">
      <div className="mx-auto flex max-w-[1800px] items-center justify-between gap-3 px-4 py-2">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded bg-sky-600 text-white" aria-hidden>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
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
