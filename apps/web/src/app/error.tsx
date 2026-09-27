"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Button } from "@/components/ui/button";

/**
 * The top-level error boundary (Request L).
 *
 * Shows the message but not the stack: a stack trace in a citizen-facing
 * register is an information leak and tells the user nothing they can act on.
 * The digest is shown instead, because it is what a support desk can correlate
 * against the server log.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    console.error("Unhandled application error:", error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-6 text-center">
      <span className="flex size-12 items-center justify-center rounded-full bg-destructive/10">
        <AlertTriangle className="size-6 text-destructive" aria-hidden />
      </span>
      <div className="space-y-1.5">
        <h1 className="text-xl font-semibold">Something went wrong</h1>
        <p className="max-w-md text-sm text-muted-foreground">
          The page could not be displayed. Nothing you had entered has been submitted.
        </p>
        {error.digest ? (
          <p className="text-xs text-muted-foreground">
            Reference <span className="ulpin">{error.digest}</span> — quote this if you
            contact support.
          </p>
        ) : null}
      </div>
      <div className="flex gap-2">
        <Button onClick={reset}>
          <RotateCcw /> Try again
        </Button>
        <Button variant="outline" asChild>
          <Link href="/dashboard">Go to dashboard</Link>
        </Button>
      </div>
    </div>
  );
}
