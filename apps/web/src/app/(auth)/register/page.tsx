"use client";

/**
 * Registration and email confirmation, on one page.
 *
 * Request: "the authentication and OTP checking should be in the registration
 * page, not a separate step." So rather than creating the account and pushing to
 * `/verify-otp`, this page holds two stages in local state:
 *
 *   1. the account form, and
 *   2. the six-digit code entry,
 *
 * swapping the second in over the first once the account exists. The address is
 * carried between them in memory, not the URL, and the user can step back to fix
 * a mistyped address without losing the rest of what they entered. The standalone
 * `/verify-otp` route still exists for the "I closed the tab" case and for
 * password resets; this simply removes the redirect for the common path.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { MailCheck } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { OtpForm } from "@/components/auth/otp-form";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Field, fieldProps } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError } from "@/lib/api/client";
import { auth } from "@/lib/api/endpoints";
import { SELF_SERVICE_ROLES } from "@/lib/constants";
import { registerSchema, type RegisterValues } from "@/lib/validators";

export default function RegisterPage() {
  const router = useRouter();
  const [formError, setFormError] = React.useState<string | null>(null);
  // Once the account is created we hold the address here and show the code step.
  // Empty string means "still on the form".
  const [pendingEmail, setPendingEmail] = React.useState<string | null>(null);

  const {
    register,
    handleSubmit,
    setError,
    setValue,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: {
      full_name: "",
      email: "",
      phone: "",
      role: "OWNER",
      password: "",
      confirm_password: "",
    },
  });

  const role = watch("role");

  async function onSubmit(values: RegisterValues) {
    setFormError(null);
    try {
      await auth.register({
        full_name: values.full_name,
        email: values.email,
        phone: values.phone || null,
        password: values.password,
        confirm_password: values.confirm_password,
        role: values.role,
      });
      toast.success("Account created. Enter the six-digit code we emailed you.");
      // Stay on this page: reveal the code step instead of navigating away.
      setPendingEmail(values.email);
    } catch (error) {
      if (error instanceof ApiError) {
        for (const [field, message] of Object.entries(error.fieldErrors)) {
          setError(field as keyof RegisterValues, { message });
        }
        setFormError(
          error.status === 409
            ? "An account with that email address already exists."
            : error.message,
        );
      } else {
        setFormError("Could not reach the server. Check your connection and try again.");
      }
    }
  }

  async function handleVerify(code: string) {
    await auth.verifyOtp(pendingEmail!, code);
    toast.success("Email confirmed. Sign in to continue.");
    router.push("/login?verified=1");
  }

  // ---- Stage two: confirm the address ------------------------------------
  if (pendingEmail) {
    return (
      <div className="space-y-6">
        <div className="flex size-11 items-center justify-center rounded-full bg-verified/10">
          <MailCheck className="size-5 text-verified" aria-hidden />
        </div>

        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">Confirm your email</h1>
          <p className="text-sm text-muted-foreground">
            We sent a six-digit code to{" "}
            <span className="font-medium text-foreground">{pendingEmail}</span>. Enter it below to
            activate your account.
          </p>
        </div>

        <Alert variant="info">
          <AlertDescription>
            You cannot sign in until this address is confirmed.
          </AlertDescription>
        </Alert>

        <OtpForm
          email={pendingEmail}
          purpose="EMAIL_VERIFY"
          onVerify={handleVerify}
          submitLabel="Confirm email"
          pendingLabel="Confirming…"
        />

        <p className="text-sm text-muted-foreground">
          Wrong address?{" "}
          <button
            type="button"
            onClick={() => setPendingEmail(null)}
            className="font-medium text-primary hover:underline"
          >
            Go back and change it
          </button>
        </p>
      </div>
    );
  }

  // ---- Stage one: the account form ---------------------------------------
  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">Create an account</h1>
        <p className="text-sm text-muted-foreground">
          For owners and tenants. Officer accounts are issued by an administrator.
        </p>
      </div>

      {formError ? (
        <Alert variant="destructive">
          <AlertDescription>{formError}</AlertDescription>
        </Alert>
      ) : null}

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <Field label="Full name" htmlFor="full_name" error={errors.full_name?.message} required>
          <Input
            {...fieldProps("full_name", errors.full_name?.message)}
            {...register("full_name")}
            autoComplete="name"
            placeholder="As it appears on your deed or agreement"
            autoFocus
          />
        </Field>

        <Field label="Email address" htmlFor="email" error={errors.email?.message} required>
          <Input
            {...fieldProps("email", errors.email?.message)}
            {...register("email")}
            type="email"
            autoComplete="username"
          />
        </Field>

        <Field
          label="Mobile number"
          htmlFor="phone"
          error={errors.phone?.message}
          hint="Optional, but it is how the office will reach you about a verification."
        >
          <Input
            {...fieldProps("phone", errors.phone?.message)}
            {...register("phone")}
            type="tel"
            autoComplete="tel"
            inputMode="numeric"
            placeholder="98XXXXXXXX"
          />
        </Field>

        <Field label="I am registering as" htmlFor="role" error={errors.role?.message} required>
          <Select
            value={role}
            onValueChange={(v) => setValue("role", v as RegisterValues["role"])}
          >
            <SelectTrigger id="role">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SELF_SERVICE_ROLES.map((r) => (
                <SelectItem key={r.value} value={r.value}>
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        <Field
          label="Password"
          htmlFor="password"
          error={errors.password?.message}
          hint="At least 12 characters, with upper case, lower case and a digit."
          required
        >
          <Input
            {...fieldProps("password", errors.password?.message)}
            {...register("password")}
            type="password"
            autoComplete="new-password"
          />
        </Field>

        <Field
          label="Confirm password"
          htmlFor="confirm_password"
          error={errors.confirm_password?.message}
          required
        >
          <Input
            {...fieldProps("confirm_password", errors.confirm_password?.message)}
            {...register("confirm_password")}
            type="password"
            autoComplete="new-password"
          />
        </Field>

        <Button type="submit" className="w-full" loading={isSubmitting}>
          {isSubmitting ? "Creating account…" : "Create account"}
        </Button>
      </form>

      <p className="text-sm text-muted-foreground">
        Already registered?{" "}
        <Link href="/login" className="font-medium text-primary hover:underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}
