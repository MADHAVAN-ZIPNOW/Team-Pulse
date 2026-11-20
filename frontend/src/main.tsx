import { Component, StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";

// Shows the error instead of a blank page if something throws while rendering.
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="mx-auto max-w-xl p-8 text-center">
        <h1 className="text-lg font-semibold text-red-300">Something went wrong</h1>
        <p className="mt-2 text-sm text-slate-400">{this.state.error.message}</p>
        <button onClick={() => window.location.reload()} className="mt-4 rounded-lg border border-slate-700 px-3 py-1.5 text-sm">
          Reload
        </button>
      </div>
    );
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
