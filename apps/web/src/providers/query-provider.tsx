"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";

import { ApiError } from "@/lib/api/client";

/**
 * React Query is the state management layer (Request L).
 *
 * Two policies worth stating:
 *
 * - `retry` refuses to retry 4xx. A 401/403/404/422 will fail identically the
 *   second time; retrying it only delays the error the user needs to see.
 * - `staleTime` is a minute. A land register does not change second to second,
 *   and an officer who tabs away and back should not trigger a refetch storm
 *   across eight dashboard queries.
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (error instanceof ApiError && error.status < 500) return false;
          return failureCount < 2;
        },
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export function QueryProvider({ children }: { children: React.ReactNode }) {
  // Created in state, not at module scope: a module-scoped client is shared
  // between requests on the server and would leak one user's cache into
  // another's render.
  const [client] = React.useState(makeQueryClient);

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
