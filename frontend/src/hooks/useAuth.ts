import { useCallback, useEffect, useState } from "react";
import { ApiError, api, setUnauthorizedHandler, type User } from "../lib/api";

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      setError("Your session expired. Please sign in again.");
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  useEffect(() => {
    // The backend redirects back with ?auth_error=... if sign-in failed.
    const params = new URLSearchParams(window.location.search);
    const authError = params.get("auth_error");
    if (authError) {
      setError(authError);
      window.history.replaceState({}, "", window.location.pathname);
    }
    api
      .me()
      .then(setUser)
      .catch((e: unknown) => {
        if (!(e instanceof ApiError && e.status === 401)) setError(e instanceof Error ? e.message : "Failed to load session");
      })
      .finally(() => setLoading(false));
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
    }
  }, []);

  return { user, loading, error, logout };
}
