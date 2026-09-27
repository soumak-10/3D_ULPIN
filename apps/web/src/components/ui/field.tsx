"use client";

import * as React from "react";
import { AlertCircle } from "lucide-react";

import { cn } from "@/lib/utils";
import { Label } from "./label";

/**
 * A label + control + error triple.
 *
 * ShadCN's `Form` wrapper is the fuller solution, but it needs a context
 * provider per form and a `FormField` render prop per input. This does the two
 * things that actually matter — associate the label with the control, and
 * announce the error to a screen reader — in a fraction of the markup.
 */
export interface FieldProps extends React.HTMLAttributes<HTMLDivElement> {
  label: string;
  htmlFor: string;
  error?: string;
  hint?: string;
  required?: boolean;
}

export function Field({
  label,
  htmlFor,
  error,
  hint,
  required,
  className,
  children,
  ...props
}: FieldProps) {
  return (
    <div className={cn("space-y-1.5", className)} {...props}>
      <Label htmlFor={htmlFor}>
        {label}
        {required ? (
          <span className="ml-0.5 text-destructive" aria-hidden>
            *
          </span>
        ) : null}
      </Label>
      {children}
      {hint && !error ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      {error ? (
        <p
          id={`${htmlFor}-error`}
          role="alert"
          className="flex items-start gap-1 text-xs font-medium text-destructive"
        >
          <AlertCircle className="mt-0.5 size-3 shrink-0" aria-hidden />
          {error}
        </p>
      ) : null}
    </div>
  );
}

/** Props to spread onto the control so it reports its own error state. */
export function fieldProps(name: string, error?: string) {
  return {
    id: name,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": error ? `${name}-error` : undefined,
  } as const;
}
