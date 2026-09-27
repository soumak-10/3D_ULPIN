"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import * as React from "react";
import {
  Box,
  Building2,
  Fingerprint,
  LayoutDashboard,
  LogOut,
  Menu,
  Moon,
  Search,
  ShieldCheck,
  Sun,
  TriangleAlert,
  Users,
  X,
} from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { NAV_ITEMS } from "@/lib/constants";
import { cn, initials } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

const ICONS: Record<string, React.ElementType> = {
  LayoutDashboard,
  Search,
  Box,
  Building2,
  Fingerprint,
  ShieldCheck,
  TriangleAlert,
  Users,
};

const ROLE_LABELS: Record<string, string> = {
  ADMIN: "Administrator",
  PROPERTY_OFFICER: "Property officer",
  OWNER: "Owner",
  TENANT: "Tenant",
};

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const [open, setOpen] = React.useState(false);

  // Close the drawer on navigation. Without this, tapping a link on a phone
  // leaves the overlay covering the page you just asked for.
  React.useEffect(() => setOpen(false), [pathname]);

  const items = NAV_ITEMS.filter((item) => !user || item.roles.includes(user.role));

  const nav = (
    <nav className="flex flex-1 flex-col gap-0.5 p-3">
      {items.map((item) => {
        const Icon = ICONS[item.icon] ?? Box;
        const active =
          pathname === item.href ||
          (item.href !== "/dashboard" && pathname.startsWith(`${item.href}/`));
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              active
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            <Icon className="size-4 shrink-0" aria-hidden />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );

  const identity = (
    <div className="border-t p-3">
      <div className="flex items-center gap-2.5 rounded-md px-1 py-1.5">
        <span
          className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground"
          aria-hidden
        >
          {initials(user?.full_name)}
        </span>
        <span className="min-w-0 flex-1 leading-tight">
          <span className="block truncate text-sm font-medium">{user?.full_name ?? "—"}</span>
          <span className="block truncate text-xs text-muted-foreground">
            {user ? (ROLE_LABELS[user.role] ?? user.role) : ""}
          </span>
        </span>
      </div>
      <div className="mt-1 flex gap-1">
        <Button
          variant="ghost"
          size="sm"
          className="flex-1 justify-start text-muted-foreground"
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        >
          {theme === "dark" ? <Sun /> : <Moon />}
          {theme === "dark" ? "Light" : "Dark"}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="flex-1 justify-start text-muted-foreground"
          onClick={() => void logout()}
        >
          <LogOut /> Sign out
        </Button>
      </div>
    </div>
  );

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      <aside className="hidden w-64 shrink-0 flex-col border-r bg-card lg:flex">
        <Link href="/dashboard" className="flex items-center gap-2.5 border-b px-4 py-4">
          <span className="flex size-8 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Box className="size-4" aria-hidden />
          </span>
          <span className="leading-tight">
            <span className="block text-sm font-semibold">3D ULPIN</span>
            <span className="block text-[11px] text-muted-foreground">
              Vertical Property Register
            </span>
          </span>
        </Link>
        {nav}
        {identity}
      </aside>

      {/* Mobile drawer */}
      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close menu"
            className="absolute inset-0 bg-black/50"
            onClick={() => setOpen(false)}
          />
          <aside className="absolute left-0 top-0 flex h-full w-72 flex-col bg-card shadow-xl">
            <div className="flex items-center justify-between border-b px-4 py-4">
              <span className="text-sm font-semibold">3D ULPIN</span>
              <Button variant="ghost" size="icon" onClick={() => setOpen(false)}>
                <X />
              </Button>
            </div>
            {nav}
            {identity}
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center gap-3 border-b bg-card px-4 lg:hidden">
          <Button variant="ghost" size="icon" onClick={() => setOpen(true)} aria-label="Open menu">
            <Menu />
          </Button>
          <span className="text-sm font-semibold">3D ULPIN</span>
        </header>

        <main id="main" className="min-w-0 flex-1 p-4 sm:p-6 lg:p-8">
          {children}
        </main>

        <footer className="border-t px-4 py-4 sm:px-6 lg:px-8">
          <Separator className="mb-3" />
          <p className="text-xs text-muted-foreground">
            Digital India Land Records Modernisation Programme · 3D ULPIN demonstration
            system. Identifiers and records shown here are for evaluation and carry no
            legal effect.
          </p>
        </footer>
      </div>
    </div>
  );
}
