"use client";

import { useRouter } from "next/navigation";
import * as React from "react";
import { Loader2 } from "lucide-react";

import { AppShell } from "@/components/layout/app-shell";
import { useAuth } from "@/providers/auth-provider";

/**
 * The authenticated shell.
 *
 * The `loading` gate matters more than it looks: on first paint there is no
 * access token in memory (it is never persisted), so `user` is null until the
 * silent refresh settles. Redirecting on a null user before then would sign
 * every user out on every page reload.
 */
export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  React.useEffect(() => {
    if (!loading && !user) {
      const next = `${window.location.pathname}${window.location.search}`;
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [loading, user, router]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center gap-2 text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden />
        <span className="text-sm">Restoring your session…</span>
      </div>
    );
  }

  return <AppShell>{children}</AppShell>;
}
