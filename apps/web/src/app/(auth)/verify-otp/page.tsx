"use client";

/**
 * Step two of registration: prove the address exists.
 *
 * The email arrives in the query string from `/register`. It is also editable
 * here, because the commonest reason a code never turns up is a typo in the
 * address — and being sent back to re-enter a whole registration form to fix one
 * character is the point at which people give up.
 */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { MailCheck } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { OtpForm } from "@/components/auth/otp-form";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, fieldProps } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { auth } from "@/lib/api/endpoints";

export default function VerifyOtpPage() {
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = React.useState(params.get("email") ?? "");
  const [confirmed, setConfirmed] = React.useState(!!params.get("email"));

  async function handleVerify(code: string) {
    await auth.verifyOtp(email, code);
    toast.success("Email confirmed. Sign in to continue.");
    router.push("/login?verified=1");
  }

  if (!confirmed) {
    return (
      <div className="space-y-6">
        <div className="space-y-1.5">
          <h1 className="text-2xl font-semibold tracking-tight">Confirm your email</h1>
          <p className="text-sm text-muted-foreground">
            Which address did you register with?
          </p>
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (email.includes("@")) setConfirmed(true);
          }}
          className="space-y-4"
          noValidate
        >
          <Field label="Email address" htmlFor="email" required>
            <Input
              {...fieldProps("email")}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              type="email"
              autoComplete="username"
              autoFocus
            />
          </Field>
          <Button type="submit" className="w-full" disabled={!email.includes("@")}>
            Continue
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

  return (
    <div className="space-y-6">
      <div className="flex size-11 items-center justify-center rounded-full bg-verified/10">
        <MailCheck className="size-5 text-verified" aria-hidden />
      </div>

      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Check your email</h1>
        <p className="text-sm text-muted-foreground">
          We sent a six-digit code to{" "}
          <span className="font-medium text-foreground">{email}</span>. Enter it below to
          activate your account.
        </p>
      </div>

      <Alert variant="info">
        <AlertDescription>
          You cannot sign in until this address is confirmed.
        </AlertDescription>
      </Alert>

      <OtpForm
        email={email}
        purpose="EMAIL_VERIFY"
        onVerify={handleVerify}
        submitLabel="Confirm email"
        pendingLabel="Confirming…"
      />

      <p className="text-sm text-muted-foreground">
        Wrong address?{" "}
        <button
          type="button"
          onClick={() => setConfirmed(false)}
          className="font-medium text-primary hover:underline"
        >
          Change it
        </button>
      </p>
    </div>
  );
}
