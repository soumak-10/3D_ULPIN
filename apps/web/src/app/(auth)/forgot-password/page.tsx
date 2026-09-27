"use client";

/**
 * Password recovery, step one and two.
 *
 * The address step deliberately reports success either way — a form that says
 * "no such account" is an account-enumeration oracle, and this register holds
 * the addresses of citizens whose property holdings are a matter of public
 * interest.
 *
 * That honesty gap is why the code step looks the way it does: it cannot say
 * "that address has no code pending" either, so a wrong code and an unknown
 * address produce the same refusal.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { MailCheck } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { OtpForm } from "@/components/auth/otp-form";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, fieldProps } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { auth } from "@/lib/api/endpoints";
import { forgotPasswordSchema, type ForgotPasswordValues } from "@/lib/validators";

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [sentTo, setSentTo] = React.useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ForgotPasswordValues>({
    resolver: zodResolver(forgotPasswordSchema),
    defaultValues: { email: "" },
  });

  async function onSubmit(values: ForgotPasswordValues) {
    // Deliberately ignores the outcome. The endpoint answers identically for a
    // known and an unknown address — including when it is throttled — and so
    // does this page.
    try {
      await auth.forgotPassword(values.email);
    } catch {
      // Swallowed for the same reason: a rate-limit or a mailer failure must not
      // become a signal about whether the address exists.
    } finally {
      setSentTo(values.email);
    }
  }

  async function handleVerify(code: string) {
    const { reset_token } = await auth.verifyResetOtp(sentTo!, code);
    toast.success("Code confirmed. Choose a new password.");
    // The code is spent server-side by the call above; what travels on is a
    // short-lived ticket, so a reload of the reset page cannot replay the code.
    router.push(`/reset-password?token=${encodeURIComponent(reset_token)}`);
  }

  if (sentTo) {
    return (
      <div className="space-y-6">
        <div className="flex size-11 items-center justify-center rounded-full bg-verified/10">
          <MailCheck className="size-5 text-verified" aria-hidden />
        </div>

        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">Check your email</h1>
          <p className="text-sm text-muted-foreground">
            If <span className="font-medium text-foreground">{sentTo}</span> belongs to an
            account, a six-digit code is on its way. Enter it below to choose a new
            password.
          </p>
        </div>

        <OtpForm
          email={sentTo}
          purpose="PASSWORD_RESET"
          onVerify={handleVerify}
          submitLabel="Continue"
          pendingLabel="Checking…"
        />

        <Alert variant="info">
          <AlertDescription>
            Nothing arrived? Check the spam folder, then contact your land records office —
            officer accounts may have password resets disabled by policy.
          </AlertDescription>
        </Alert>

        <div className="flex items-center justify-between text-sm">
          <button
            type="button"
            onClick={() => setSentTo(null)}
            className="font-medium text-primary hover:underline"
          >
            Use a different address
          </button>
          <Link href="/login" className="text-muted-foreground hover:underline">
            Back to sign in
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">Reset your password</h1>
        <p className="text-sm text-muted-foreground">
          Enter your registered email address and we will send a six-digit code.
        </p>
      </div>

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Field label="Email address" htmlFor="email" error={errors.email?.message} required>
          <Input
            {...fieldProps("email", errors.email?.message)}
            {...register("email")}
            type="email"
            autoComplete="username"
            autoFocus
          />
        </Field>

        <Button type="submit" className="w-full" loading={isSubmitting}>
          {isSubmitting ? "Sending…" : "Send reset code"}
        </Button>
      </form>

      <p className="text-sm text-muted-foreground">
        <Link href="/login" className="font-medium text-primary hover:underline">
          Back to sign in
        </Link>
      </p>
    </div>
  );
}
