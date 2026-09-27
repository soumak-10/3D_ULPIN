"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import {
  Check,
  CircleAlert,
  CircleHelp,
  Fingerprint,
  ShieldCheck,
  X,
} from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import {
  EmptyState,
  ErrorState,
  PageHeader,
  StatusBadge,
  UlpinChip,
} from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, fieldProps } from "@/components/ui/field";
import { Input, Textarea } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { verification } from "@/lib/api/endpoints";
import { useAuth } from "@/providers/auth-provider";
import { errorMessage } from "@/lib/api/client";
import { verifySchema, type VerifyValues } from "@/lib/validators";
import { cn, formatDateTime, formatPercent, OUTCOME_LABELS, statusTone } from "@/lib/utils";
import type { CheckResponse, VerificationResponse } from "@/types/api";

/**
 * The verification engine's front end (Request I).
 *
 * The workflow is the one the engine runs: identifier in, property fetched,
 * geometry fetched, ownership validated, tenancy validated, verdict out. The
 * page shows all five checks whatever the verdict, because "why" is the part an
 * officer has to defend and a bare green tick cannot be defended.
 */

const CHECK_LABELS: Record<string, string> = {
  property: "1. Property record",
  geometry: "2. Geometry",
  ownership: "3. Ownership",
  tenancy: "4. Tenancy",
  tenant: "4. Tenancy",
  occupancy: "5. Occupancy",
  ulpin: "0. Identifier",
};

function CheckRow({ check }: { check: CheckResponse }) {
  // A failed check that is not blocking means the register is missing data, not
  // that the claim is false — those two must not look the same on screen.
  const tone = check.passed ? "verified" : check.blocking ? "fraud" : "pending";
  const Icon = check.passed ? Check : check.blocking ? X : CircleHelp;
  const label =
    CHECK_LABELS[check.check] ?? check.check.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

  return (
    <li className="flex gap-3 border-b py-3 last:border-0">
      <span
        className={cn(
          "mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full",
          tone === "verified" && "bg-verified/15 text-verified",
          tone === "pending" && "bg-pending/15 text-pending",
          tone === "fraud" && "bg-fraud/15 text-fraud",
        )}
        aria-hidden
      >
        <Icon className="size-3.5" strokeWidth={3} />
      </span>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium">{label}</p>
          {!check.passed ? (
            <Badge variant={check.blocking ? "fraud" : "pending"}>
              {check.blocking ? "contradicted" : "inconclusive"}
            </Badge>
          ) : null}
        </div>
        <p className="text-sm text-muted-foreground">{check.detail}</p>
        {Object.keys(check.evidence ?? {}).length > 0 ? (
          <dl className="flex flex-wrap gap-x-4 gap-y-1 pt-0.5 text-xs text-muted-foreground">
            {Object.entries(check.evidence).map(([key, value]) => (
              <div key={key} className="flex gap-1">
                <dt className="font-medium">{key.replace(/_/g, " ")}:</dt>
                <dd className="tabular-nums">
                  {typeof value === "object" ? JSON.stringify(value) : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>
    </li>
  );
}

const OUTCOME_COPY: Record<string, string> = {
  VERIFIED: "Every check passed. The record, its geometry, its ownership and its occupancy agree.",
  PENDING_VERIFICATION:
    "Nothing contradicts the record, but something needed is missing. This is not an adverse finding — it is an incomplete one.",
  INVALID_CLAIM: "The register contradicts the claim. Do not act on this identifier without a manual review.",
  UNAUTHORIZED_OCCUPANCY:
    "The unit is occupied by someone with no recorded right to be there. The ownership record itself may still be sound.",
};

function Verdict({ result }: { result: VerificationResponse }) {
  const tone = statusTone(result.outcome);

  return (
    <Card
      className={cn(
        "border-l-4",
        tone === "verified" && "border-l-verified",
        tone === "pending" && "border-l-pending",
        tone === "fraud" && "border-l-fraud",
      )}
    >
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-lg">{OUTCOME_LABELS[result.outcome] ?? result.outcome}</CardTitle>
              <StatusBadge outcome={result.outcome} />
            </div>
            <CardDescription>{result.headline}</CardDescription>
          </div>
          <div className="text-right">
            <p className="text-2xl font-semibold tabular-nums">{formatPercent(result.confidence)}</p>
            <p className="text-xs text-muted-foreground">confidence</p>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="rounded-md bg-muted/60 p-3 text-sm">{OUTCOME_COPY[result.outcome]}</p>

        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Identifier</dt>
            <dd className="pt-0.5">
              <UlpinChip code={result.short_code ?? result.ulpin_code} />
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Building</dt>
            <dd className="pt-0.5">{result.building_name ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Floor / unit</dt>
            <dd className="pt-0.5">
              {result.floor_number !== null ? `Floor ${result.floor_number}` : "—"}
              {result.unit_number ? ` · ${result.unit_number}` : ""}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Owner</dt>
            <dd className="pt-0.5">
              {result.owner_names.length > 0 ? result.owner_names.join(", ") : "not recorded"}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Tenant</dt>
            <dd className="pt-0.5">{result.tenant_name ?? "none"}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Status</dt>
            <dd className="pt-0.5">
              {result.occupancy_status?.replace(/_/g, " ").toLowerCase() ?? "—"}
            </dd>
          </div>
        </dl>

        <div>
          <div className="flex items-center justify-between pb-1">
            <h3 className="text-sm font-semibold">Checks</h3>
            <p className="text-xs text-muted-foreground tabular-nums">
              {result.checks_passed} passed · {result.checks_failed} not passed
            </p>
          </div>
          <ul>
            {result.checks.map((c) => (
              <CheckRow key={c.check} check={c} />
            ))}
          </ul>
        </div>

        {result.verified_at ? (
          <p className="text-xs text-muted-foreground">
            Recorded {formatDateTime(result.verified_at)}
            {result.verification_id ? (
              <> · reference <span className="ulpin">{result.verification_id.slice(0, 8)}</span></>
            ) : null}
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">
            Not persisted — a verification run is only written when it reaches a decision.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export default function VerificationPage() {
  const { isStaff } = useAuth();
  const [result, setResult] = React.useState<VerificationResponse | null>(null);
  const [submitted, setSubmitted] = React.useState<string | null>(null);

  const form = useForm<VerifyValues>({
    resolver: zodResolver(verifySchema),
    defaultValues: { ulpin: "", claimed_owner_name: "", claimed_tenant_name: "", remarks: "" },
  });

  const run = useMutation({
    mutationFn: (values: VerifyValues) =>
      verification.verify(values.ulpin, {
        claimed_owner_name: values.claimed_owner_name?.trim() || null,
        claimed_tenant_name: values.claimed_tenant_name?.trim() || null,
        remarks: values.remarks?.trim() || null,
      }),
    onSuccess: (data) => {
      setResult(data);
      setSubmitted(data.short_code ?? data.ulpin_code);
    },
    onError: (e) => {
      setResult(null);
      toast.error(errorMessage(e));
    },
  });

  const history = useQuery({
    queryKey: ["verification-history", submitted],
    queryFn: () => verification.history(submitted!),
    enabled: Boolean(submitted),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Verification"
        description="Enter an identifier. The engine fetches the property and its geometry, then validates ownership and tenancy."
      />

      <div className="grid gap-6 lg:grid-cols-[22rem_1fr]">
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Verify an identifier</CardTitle>
              <CardDescription>Either the short form or the 14-character parcel form.</CardDescription>
            </CardHeader>
            <CardContent>
              <form
                onSubmit={form.handleSubmit((v) => run.mutate(v))}
                className="space-y-4"
                noValidate
              >
                <Field
                  label="ULPIN"
                  htmlFor="ulpin"
                  required
                  error={form.formState.errors.ulpin?.message}
                >
                  <Input
                    {...form.register("ulpin")}
                    {...fieldProps("ulpin", form.formState.errors.ulpin?.message)}
                    placeholder="WB-KOL-B001-F03-U301"
                    className="ulpin uppercase"
                    autoComplete="off"
                    autoFocus
                  />
                </Field>

                <Field
                  label="Claimed owner"
                  htmlFor="claimed_owner_name"
                  hint="Optional. Supply it to test a claim against the register rather than only reading the register."
                  error={form.formState.errors.claimed_owner_name?.message}
                >
                  <Input {...form.register("claimed_owner_name")} placeholder="Rina Banerjee" />
                </Field>

                <Field
                  label="Claimed tenant"
                  htmlFor="claimed_tenant_name"
                  error={form.formState.errors.claimed_tenant_name?.message}
                >
                  <Input {...form.register("claimed_tenant_name")} placeholder="Arjun Mehta" />
                </Field>

                {isStaff ? (
                  <Field label="Remarks" htmlFor="remarks" hint="Written to the audit log.">
                    <Textarea {...form.register("remarks")} rows={2} />
                  </Field>
                ) : null}

                <Button type="submit" className="w-full" loading={run.isPending}>
                  <ShieldCheck /> Verify
                </Button>
              </form>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-2 p-4 text-xs text-muted-foreground">
              <p className="font-medium text-foreground">The four outcomes</p>
              <p><span className="text-verified">Verified</span> — every check agrees.</p>
              <p><span className="text-pending">Pending verification</span> — data missing, nothing contradicted.</p>
              <p><span className="text-fraud">Invalid claim</span> — the register contradicts the claim.</p>
              <p><span className="text-fraud">Unauthorised occupancy</span> — occupied with no recorded right.</p>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          {run.isPending ? (
            <Card>
              <CardContent className="space-y-3 p-6">
                <Skeleton className="h-6 w-48" />
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-32 w-full" />
              </CardContent>
            </Card>
          ) : run.isError ? (
            <ErrorState error={run.error} onRetry={() => form.handleSubmit((v) => run.mutate(v))()} />
          ) : result ? (
            <Verdict result={result} />
          ) : (
            <EmptyState
              icon={Fingerprint}
              title="No identifier verified yet"
              description="The verdict, all five checks and the evidence behind each will appear here."
            />
          )}

          {submitted ? (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Previous runs</CardTitle>
                <CardDescription>
                  Every verification of <span className="ulpin">{submitted}</span>, newest first
                </CardDescription>
              </CardHeader>
              <CardContent className="pt-0">
                {history.isLoading ? (
                  <Skeleton className="h-24" />
                ) : (history.data?.length ?? 0) === 0 ? (
                  <p className="py-4 text-sm text-muted-foreground">
                    This is the first recorded verification of this identifier.
                  </p>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Outcome</TableHead>
                        <TableHead>Method</TableHead>
                        <TableHead>Confidence</TableHead>
                        <TableHead>Checks</TableHead>
                        <TableHead>When</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {history.data?.map((row) => (
                        <TableRow key={row.verification_id}>
                          <TableCell>
                            <StatusBadge outcome={row.outcome} />
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            {row.method?.replace(/_/g, " ").toLowerCase() ?? "—"}
                          </TableCell>
                          <TableCell className="tabular-nums text-muted-foreground">
                            {formatPercent(row.confidence)}
                          </TableCell>
                          <TableCell className="tabular-nums text-muted-foreground">
                            {row.checks_passed ?? 0}/
                            {(row.checks_passed ?? 0) + (row.checks_failed ?? 0)}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-muted-foreground">
                            {formatDateTime(row.verified_at ?? row.created_at)}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </CardContent>
            </Card>
          ) : null}

          {result && result.outcome !== "VERIFIED" && isStaff ? (
            <p className="flex items-start gap-2 text-xs text-muted-foreground">
              <CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              An adverse or incomplete verdict does not itself raise an alert. Run a fraud scan
              from the alerts queue if the pattern needs to be recorded against the property.
            </p>
          ) : null}
        </div>
      </div>
    </div>
  );
}
