import { useAuth } from "./hooks/useAuth";
import { Spinner } from "./components/common";
import { LoginPage } from "./pages/LoginPage";
import { MainPage } from "./pages/MainPage";

export default function App() {
  const { user, loading, error, logout } = useAuth();
  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Checking session…" />
      </div>
    );
  }
  return user ? <MainPage user={user} onLogout={logout} /> : <LoginPage error={error} />;
}
