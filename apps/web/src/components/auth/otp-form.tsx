"use client";

/**
 * Shared six-digit code entry.
 *
 * Both OTP flows — confirming an address after registration, and proving
 * ownership of a mailbox before a password reset — present the same form and
 * the same resend affordance. What differs is only what a correct code buys,
 * so the caller supplies `onVerify` and this component owns the field, the
 * error surface and the cooldown.
 */

import { RotateCw } from "lucide-react";
import * as React from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, fieldProps } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError } from "@/lib/api/client";
import { auth } from "@/lib/api/endpoints";

/** Mirrors OTP_RESEND_COOLDOWN_SECONDS on the API. */
const RESEND_COOLDOWN_SECONDS = 60;

export interface OtpFormProps {
  email: string;
  purpose: "EMAIL_VERIFY" | "PASSWORD_RESET";
  /** Runs with the normalised six digits. Throw to surface an error. */
  onVerify: (code: string) => Promise<void>;
  submitLabel?: string;
  pendingLabel?: string;
}

export function OtpForm({
  email,
  purpose,
  onVerify,
  submitLabel = "Verify",
  pendingLabel = "Verifying…",
}: OtpFormProps) {
  const [code, setCode] = React.useState("");
  const [fieldError, setFieldError] = React.useState<string | null>(null);
  const [formError, setFormError] = React.useState<string | null>(null);
  const [notice, setNotice] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const [cooldown, setCooldown] = React.useState(0);

  // The countdown is cosmetic — the API enforces the same window — but without
  // it the resend button looks broken rather than deliberately unavailable.
  React.useEffect(() => {
    if (cooldown <= 0) return;
    const id = window.setTimeout(() => setCooldown((s) => s - 1), 1000);
    return () => window.clearTimeout(id);
  }, [cooldown]);

  const digits = code.replace(/[\s-]/g, "");

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setFormError(null);
    setNotice(null);

    if (!/^\d{6}$/.test(digits)) {
      setFieldError("The code is six digits.");
      return;
    }
    setFieldError(null);
    setBusy(true);
    try {
      await onVerify(digits);
    } catch (error) {
      if (error instanceof ApiError) {
        // The API counts attempts and says how many remain, which is worth
        // showing: a user who mistypes twice should know the code is about to
        // burn rather than discovering it silently.
        setFieldError(error.message);
      } else {
        setFormError("Could not reach the server. Check your connection and try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleResend() {
    setFormError(null);
    setFieldError(null);
    setBusy(true);
    try {
      await auth.resendOtp(email, purpose);
      setCode("");
      setCooldown(RESEND_COOLDOWN_SECONDS);
      setNotice(`A new code is on its way to ${email}. The previous one no longer works.`);
    } catch (error) {
      if (error instanceof ApiError && error.status === 429) {
        setCooldown(RESEND_COOLDOWN_SECONDS);
        setFormError(error.message);
      } else if (error instanceof ApiError) {
        setFormError(error.message);
      } else {
        setFormError("Could not reach the server. Check your connection and try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {formError ? (
        <Alert variant="destructive">
          <AlertDescription>{formError}</AlertDescription>
        </Alert>
      ) : null}

      {notice ? (
        <Alert variant="success">
          <AlertDescription>{notice}</AlertDescription>
        </Alert>
      ) : null}

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <Field
          label="Six-digit code"
          htmlFor="code"
          error={fieldError ?? undefined}
          hint="Valid for 10 minutes. Check your spam folder if it has not arrived."
          required
        >
          <Input
            {...fieldProps("code", fieldError ?? undefined)}
            value={code}
            onChange={(event) => setCode(event.target.value)}
            // `one-time-code` is what lets the OS offer the code from the
            // notification instead of making the user switch apps to copy it.
            autoComplete="one-time-code"
            inputMode="numeric"
            maxLength={8}
            placeholder="000000"
            autoFocus
            className="text-center text-2xl tracking-[0.5em] font-mono"
          />
        </Field>

        <Button type="submit" className="w-full" loading={busy} disabled={busy}>
          {busy ? pendingLabel : submitLabel}
        </Button>
      </form>

      <div className="flex items-center justify-between text-sm">
        <span className="text-muted-foreground">Did not get the code?</span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={handleResend}
          disabled={busy || cooldown > 0}
        >
          <RotateCw className="mr-1.5 size-3.5" aria-hidden />
          {cooldown > 0 ? `Resend in ${cooldown}s` : "Send a new code"}
        </Button>
      </div>
    </div>
  );
}
