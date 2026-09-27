"use client";

import { useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import {
  ApiError,
  bootstrapSession,
  setAccessToken,
  setUnauthenticatedHandler,
} from "@/lib/api/client";
import { auth } from "@/lib/api/endpoints";
import type { Role, UserResponse } from "@/types/api";

interface AuthState {
  user: UserResponse | null;
  /** True until the silent refresh at boot has settled, either way. */
  loading: boolean;
  login: (email: string, password: string) => Promise<UserResponse>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  hasRole: (...roles: Role[]) => boolean;
  isStaff: boolean;
}

const AuthContext = React.createContext<AuthState | null>(null);

/**
 * The session.
 *
 * The access token never leaves memory (see lib/api/client). On first paint we
 * therefore have no token and must ask the refresh cookie for one — that is the
 * `loading` window, and it is why the shell renders a skeleton rather than
 * bouncing to /login: redirecting on a null user before the refresh settles
 * logs out every user on every reload.
 */
export function AuthProvider({
  children,
  initialUser = null,
}: {
  children: React.ReactNode;
  initialUser?: UserResponse | null;
}) {
  const router = useRouter();
  const [user, setUser] = React.useState<UserResponse | null>(initialUser);
  const [loading, setLoading] = React.useState(true);

  // A 401 that survives a refresh attempt means the session is genuinely gone.
  // Clear it here rather than in the client, which should not know about React.
  React.useEffect(() => {
    setUnauthenticatedHandler(() => {
      setUser(null);
      document.cookie = "ulpin_role=; Path=/; Max-Age=0; SameSite=Lax";
    });
    return () => setUnauthenticatedHandler(null);
  }, []);

  React.useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const token = await bootstrapSession();
        if (cancelled) return;
        if (!token) {
          setUser(null);
          return;
        }
        const me = await auth.me();
        if (cancelled) return;
        setUser(me);
        document.cookie = `ulpin_role=${me.role}; Path=/; Max-Age=86400; SameSite=Lax`;
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const login = React.useCallback(
    async (email: string, password: string) => {
      const res = await auth.login(email, password);
      setAccessToken(res.access_token);
      setUser(res.user);
      // A readable role cookie, so middleware can gate staff-only routes
      // without a round trip. It is a hint, not a credential: every endpoint
      // still checks the JWT's role server-side.
      document.cookie = `ulpin_role=${res.user.role}; Path=/; Max-Age=86400; SameSite=Lax`;
      return res.user;
    },
    [],
  );

  const logout = React.useCallback(async () => {
    try {
      await auth.logout();
    } catch (error) {
      // A failed logout still has to clear the client. Leaving the user
      // "logged in" because the revocation call timed out is the wrong way
      // round — better a dead token on the server than a live session here.
      if (!(error instanceof ApiError) || error.status >= 500) {
        toast.error("Signed out locally; the server could not be reached.");
      }
    } finally {
      setAccessToken(null);
      setUser(null);
      document.cookie = "ulpin_role=; Path=/; Max-Age=0; SameSite=Lax";
      router.push("/login");
    }
  }, [router]);

  const refresh = React.useCallback(async () => {
    try {
      setUser(await auth.me());
    } catch {
      setUser(null);
    }
  }, []);

  const hasRole = React.useCallback(
    (...roles: Role[]) => (user ? roles.includes(user.role) : false),
    [user],
  );

  const value = React.useMemo<AuthState>(
    () => ({
      user,
      loading,
      login,
      logout,
      refresh,
      hasRole,
      isStaff: user ? user.role === "ADMIN" || user.role === "PROPERTY_OFFICER" : false,
    }),
    [user, loading, login, logout, refresh, hasRole],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>.");
  return ctx;
}
