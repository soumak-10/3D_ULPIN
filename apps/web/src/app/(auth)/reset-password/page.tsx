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
import { ApiError } from "@/lib/api/client";
import { auth } from "@/lib/api/endpoints";
import { resetPasswordSchema, type ResetPasswordValues } from "@/lib/validators";

export default function ResetPasswordPage() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const [formError, setFormError] = React.useState<string | null>(null);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<ResetPasswordValues>({
    resolver: zodResolver(resetPasswordSchema),
    defaultValues: { token, password: "", confirm_password: "" },
  });

  async function onSubmit(values: ResetPasswordValues) {
    setFormError(null);
    try {
      await auth.resetPassword(values.token, values.password);
      toast.success("Password changed. Sign in with the new one.");
      router.push("/login?reset=1");
    } catch (error) {
      if (error instanceof ApiError) {
        for (const [field, message] of Object.entries(error.fieldErrors)) {
          setError(field as keyof ResetPasswordValues, { message });
        }
        setFormError(
          error.status === 400 || error.status === 410
            ? "This reset link has expired or has already been used. Request a new one."
            : error.message,
        );
      } else {
        setFormError("Could not reach the server. Check your connection and try again.");
      }
    }
  }

  if (!token) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold tracking-tight">Start the reset again</h1>
        <Alert variant="destructive">
          <AlertDescription>
            This page needs a verified reset code. Request one and enter it, and you will
            arrive back here ready to choose a password.
          </AlertDescription>
        </Alert>
        <Button asChild className="w-full">
          <Link href="/forgot-password">Request a reset code</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <h1 className="text-2xl font-semibold tracking-tight">Choose a new password</h1>
        <p className="text-sm text-muted-foreground">
          Setting a new password signs out every other device.
        </p>
      </div>

      {formError ? (
        <Alert variant="destructive">
          <AlertDescription>{formError}</AlertDescription>
        </Alert>
      ) : null}

      <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
        <input type="hidden" {...register("token")} />

        <Field
          label="New password"
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
            autoFocus
          />
        </Field>

        <Field
          label="Confirm new password"
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
          {isSubmitting ? "Saving…" : "Set new password"}
        </Button>
      </form>
    </div>
  );
}
