"use client";

import { ThemeProvider } from "next-themes";
import * as React from "react";

import { Toaster } from "@/components/ui/toaster";
import { AuthProvider } from "./auth-provider";
import { QueryProvider } from "./query-provider";

/**
 * Every client-side provider, in one component.
 *
 * Order matters: the theme provider wraps the toaster (which reads the theme),
 * and the query provider wraps auth (which issues queries on mount).
 */
export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem disableTransitionOnChange>
      <QueryProvider>
        <AuthProvider>
          {children}
          <Toaster />
        </AuthProvider>
      </QueryProvider>
    </ThemeProvider>
  );
}
