"use client";

import Link from "next/link";
import { AlertTriangle, Check, Copy, FileQuestion, Inbox, RotateCcw } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PAGE_SIZES } from "@/lib/constants";
import { ApiError, errorMessage } from "@/lib/api/client";
import { OUTCOME_LABELS, SEVERITY_TONE, cn, statusTone } from "@/lib/utils";

/* ------------------------------------------------------------ status badge -- */

/**
 * The verdict, rendered in the specification's colours.
 *
 * Green = Verified, Yellow = Pending, Red = Fraud. Every surface in the
 * application uses this component rather than picking a colour locally, so the
 * three meanings cannot drift apart.
 */
export function StatusBadge({
  outcome,
  openAlerts = 0,
  className,
}: {
  outcome: string | null | undefined;
  openAlerts?: number;
  className?: string;
}) {
  const tone = statusTone(outcome, openAlerts);
  const label =
    openAlerts > 0 && outcome !== "INVALID_CLAIM" && outcome !== "UNAUTHORIZED_OCCUPANCY"
      ? `Flagged (${openAlerts})`
      : (OUTCOME_LABELS[outcome ?? ""] ?? "Not verified");

  return (
    <Badge variant={tone} className={className}>
      {label}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  return <Badge variant={SEVERITY_TONE[severity] ?? "neutral"}>{severity.toLowerCase()}</Badge>;
}

/** The colour key, shown wherever the coding first appears on a page. */
export function StatusLegend({ className }: { className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-3 text-xs text-muted-foreground", className)}>
      <span className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-full bg-verified" aria-hidden /> Verified
      </span>
      <span className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-full bg-pending" aria-hidden /> Pending
      </span>
      <span className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-full bg-fraud" aria-hidden /> Fraud
      </span>
    </div>
  );
}

/* -------------------------------------------------------------- ulpin chip -- */

/**
 * An identifier, monospaced and copyable.
 *
 * Officers read these aloud over the phone and type them into other systems;
 * a one-click copy removes the transcription error that a 21-character string
 * otherwise guarantees.
 */
export function UlpinChip({
  code,
  className,
  href,
}: {
  code: string | null | undefined;
  className?: string;
  href?: string;
}) {
  const [copied, setCopied] = React.useState(false);

  if (!code) return <span className="text-sm text-muted-foreground">Not issued</span>;

  const body = <span className="ulpin text-sm">{code}</span>;

  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      {href ? (
        <Link href={href} className="hover:underline">
          {body}
        </Link>
      ) : (
        body
      )}
      <button
        type="button"
        aria-label={`Copy ${code}`}
        className="rounded p-0.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          void navigator.clipboard.writeText(code).then(() => {
            setCopied(true);
            toast.success("ULPIN copied");
            setTimeout(() => setCopied(false), 1500);
          });
        }}
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      </button>
    </span>
  );
}

/* ------------------------------------------------------------ page header -- */

export function PageHeader({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description ? (
          <p className="max-w-2xl text-sm text-muted-foreground">{description}</p>
        ) : null}
      </div>
      {children ? <div className="flex shrink-0 items-center gap-2">{children}</div> : null}
    </div>
  );
}

/* ------------------------------------------------------------ empty/error -- */

export function EmptyState({
  title,
  description,
  icon: Icon = Inbox,
  children,
}: {
  title: string;
  description?: string;
  icon?: React.ElementType;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-6 py-14 text-center">
      <Icon className="size-8 text-muted-foreground" aria-hidden />
      <p className="font-medium">{title}</p>
      {description ? (
        <p className="max-w-sm text-sm text-muted-foreground">{description}</p>
      ) : null}
      {children}
    </div>
  );
}

/**
 * The error panel.
 *
 * A 403 is separated from everything else on purpose: "you do not have
 * permission" is actionable (ask an administrator) while "something went wrong"
 * is not, and a retry button on a 403 just makes the user click it twice.
 */
export function ErrorState({
  error,
  onRetry,
  className,
}: {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}) {
  const forbidden = error instanceof ApiError && error.isForbidden;
  const missing = error instanceof ApiError && error.isNotFound;

  if (missing) {
    return (
      <EmptyState
        icon={FileQuestion}
        title="Not found"
        description={errorMessage(error)}
      />
    );
  }

  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-12 text-center",
        className,
      )}
    >
      <AlertTriangle className="size-8 text-destructive" aria-hidden />
      <div className="space-y-1">
        <p className="font-medium text-destructive">
          {forbidden ? "You do not have permission to view this" : "Could not load this"}
        </p>
        <p className="max-w-md text-sm text-muted-foreground">{errorMessage(error)}</p>
      </div>
      {onRetry && !forbidden ? (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RotateCcw /> Try again
        </Button>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------- pagination -- */

export function Pagination({
  page,
  pages,
  total,
  pageSize,
  onPageChange,
  onPageSizeChange,
}: {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
}) {
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  return (
    <div className="flex flex-col gap-3 border-t pt-3 text-sm sm:flex-row sm:items-center sm:justify-between">
      <p className="text-muted-foreground">
        {total === 0 ? "No results" : `Showing ${from}–${to} of ${total.toLocaleString("en-IN")}`}
      </p>

      <div className="flex items-center gap-2">
        {onPageSizeChange ? (
          <Select value={String(pageSize)} onValueChange={(v) => onPageSizeChange(Number(v))}>
            <SelectTrigger className="h-9 w-[7.5rem]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAGE_SIZES.map((s) => (
                <SelectItem key={s} value={String(s)}>
                  {s} per page
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : null}

        <Button
          variant="outline"
          size="sm"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          Previous
        </Button>
        <span className="min-w-[5rem] text-center text-muted-foreground">
          Page {page} of {Math.max(pages, 1)}
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </Button>
      </div>
    </div>
  );
}
