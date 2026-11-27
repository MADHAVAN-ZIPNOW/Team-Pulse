import type { User } from "../lib/api";
import { Avatar, MsLogo } from "./common";

export function Header({ user, onLogout }: { user: User; onLogout: () => void }) {
  return (
    <header className="sticky top-0 z-10 border-b border-slate-800 bg-white">
      <div className="mx-auto flex max-w-[1800px] items-center justify-between gap-3 px-4 py-2">
        <div className="flex items-center gap-2">
          <MsLogo size={22} />
          <h1 className="text-base font-semibold text-slate-100">Team Pulse</h1>
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
