"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, fieldProps } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { ApiError } from "@/lib/api/client";
import { loginSchema, type LoginValues } from "@/lib/validators";
import { useAuth } from "@/providers/auth-provider";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const { login } = useAuth();
  const [formError, setFormError] = React.useState<string | null>(null);
  const [unverified, setUnverified] = React.useState<string | null>(null);

  const next = params.get("next");
  const justRegistered = params.get("registered") === "1";
  const justVerified = params.get("verified") === "1";
  const justReset = params.get("reset") === "1";

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  async function onSubmit(values: LoginValues) {
    setFormError(null);
    setUnverified(null);
    try {
      const user = await login(values.email, values.password);
      toast.success(`Signed in as ${user.full_name}`);
      router.push(next && next.startsWith("/") ? next : "/dashboard");
    } catch (error) {
      if (error instanceof ApiError) {
        for (const [field, message] of Object.entries(error.fieldErrors)) {
          setError(field as keyof LoginValues, { message });
        }
        // 403 email_not_verified is the one refusal worth naming precisely: the
        // user holds the right password and the fix is in their inbox, so a
        // generic "those credentials do not match" would send them off to reset
        // a password that was never the problem.
        if (error.status === 403) {
          setUnverified(values.email);
          return;
        }
        // A 401 here is a wrong password, and the message deliberately does not
        // say which of the two was wrong: "no such account" tells an attacker
        // which addresses are registered.
        setFormError(
          error.status === 401
            ? "Those credentials do not match an active account."
            : error.status === 423
              ? "This account is locked. Contact your administrator."
              : error.message,
        );
      } else {
        setFormError("Could not reach the server. Check your connection and try again.");
      }
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">Sign in</h1>
        <p className="text-sm text-muted-foreground">
          Use the credentials issued by your land records office.
        </p>
      </div>

      {justRegistered ? (
        <Alert variant="success">
          <AlertDescription>
            Your account has been created. Sign in to continue.
          </AlertDescription>
        </Alert>
      ) : null}

      {justVerified ? (
        <Alert variant="success">
          <AlertDescription>
            Your email address has been confirmed. Sign in to continue.
          </AlertDescription>
        </Alert>
      ) : null}

      {justReset ? (
        <Alert variant="success">
          <AlertDescription>
            Your password has been changed. Sign in with the new one.
          </AlertDescription>
        </Alert>
      ) : null}

      {unverified ? (
        <Alert variant="warning">
          <AlertDescription className="space-y-2">
            <p>Please verify your email before login.</p>
            <Button asChild size="sm" variant="outline">
              <Link href={`/verify-otp?email=${encodeURIComponent(unverified)}`}>
                Enter the code sent to {unverified}
              </Link>
            </Button>
          </AlertDescription>
        </Alert>
      ) : null}

      {formError ? (
        <Alert variant="destructive">
          <AlertDescription>{formError}</AlertDescription>
        </Alert>
      ) : null}

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Field label="Email address" htmlFor="email" error={errors.email?.message} required>
          <Input
            {...fieldProps("email", errors.email?.message)}
            {...register("email")}
            type="email"
            autoComplete="username"
            placeholder="officer@landrecords.gov.in"
            autoFocus
          />
        </Field>

        <Field label="Password" htmlFor="password" error={errors.password?.message} required>
          <PasswordInput
            {...fieldProps("password", errors.password?.message)}
            {...register("password")}
            autoComplete="current-password"
          />
        </Field>

        <div className="flex items-center justify-between text-sm">
          <Link href="/forgot-password" className="text-primary hover:underline">
            Forgot your password?
          </Link>
        </div>

        <Button type="submit" className="w-full" loading={isSubmitting}>
          {isSubmitting ? "Signing in…" : "Sign in"}
        </Button>
      </form>

      <p className="text-sm text-muted-foreground">
        Are you an owner or tenant without an account?{" "}
        <Link href="/register" className="font-medium text-primary hover:underline">
          Register
        </Link>
      </p>
    </div>
  );
}
