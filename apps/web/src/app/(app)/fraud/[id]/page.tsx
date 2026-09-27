"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, Gavel, ShieldCheck, UserCheck } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { ErrorState, PageHeader, SeverityBadge, UlpinChip } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, fieldProps } from "@/components/ui/field";
import { Textarea } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage } from "@/lib/api/client";
import { fraud } from "@/lib/api/endpoints";
import { ALERT_STATUSES } from "@/lib/constants";
import { formatDateTime, formatPercent } from "@/lib/utils";
import { alertDecisionSchema, clean, type AlertDecisionValues } from "@/lib/validators";
import { useAuth } from "@/providers/auth-provider";

/**
 * One alert, with its evidence and the decision form (Request J).
 *
 * The evidence is rendered as the engine recorded it rather than prettified into
 * prose: an officer defending a decision needs the values the rule actually
 * compared, not a paraphrase of them.
 */
export default function AlertDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { user } = useAuth();

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["fraud-alert", id],
    queryFn: () => fraud.alert(id),
  });

  const form = useForm<AlertDecisionValues>({
    resolver: zodResolver(alertDecisionSchema),
    defaultValues: { status: "INVESTIGATING", resolution_notes: "", is_false_positive: false },
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["fraud-alert", id] });
    void qc.invalidateQueries({ queryKey: ["fraud-alerts"] });
    void qc.invalidateQueries({ queryKey: ["fraud-summary"] });
  };

  const decide = useMutation({
    mutationFn: (values: AlertDecisionValues) => fraud.decide(id, clean(values) as never),
    onSuccess: (a) => {
      toast.success(`Alert marked ${a.status.toLowerCase()}.`);
      invalidate();
      form.reset({ status: "INVESTIGATING", resolution_notes: "", is_false_positive: false });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const assign = useMutation({
    mutationFn: () => fraud.assign(id, user!.user_id),
    onSuccess: () => {
      toast.success("Assigned to you.");
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (isError) {
    return (
      <div className="space-y-6">
        <PageHeader title="Alert" />
        <ErrorState error={error} onRetry={() => void refetch()} />
      </div>
    );
  }

  if (isLoading || !data) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const closed = ["DISMISSED", "RESOLVED"].includes(data.status);

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" className="-ml-2" onClick={() => router.back()}>
        <ArrowLeft /> Back to queue
      </Button>

      <PageHeader title={data.title} description={data.description ?? undefined}>
        <SeverityBadge severity={data.severity} />
        <Badge variant="outline">{data.status.toLowerCase()}</Badge>
        {data.is_false_positive ? <Badge variant="neutral">false positive</Badge> : null}
      </PageHeader>

      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Finding</CardTitle>
              <CardDescription>
                Rule {data.rule_code.replace(/_/g, " ").toLowerCase()} — severity is fixed by the
                rule and cannot be edited per alert
              </CardDescription>
            </CardHeader>
            <CardContent>
              <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">Identifier</dt>
                  <dd className="pt-0.5">
                    <UlpinChip code={data.short_code} />
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">Property</dt>
                  <dd className="pt-0.5">
                    {data.building_id ? (
                      <Link href={`/buildings/${data.building_id}`} className="hover:underline">
                        {data.building_name ?? "Building"}
                      </Link>
                    ) : (
                      (data.building_name ?? "—")
                    )}
                    {data.unit_number ? ` · unit ${data.unit_number}` : ""}
                    {data.floor_number !== null ? ` · floor ${data.floor_number}` : ""}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">Confidence</dt>
                  <dd className="pt-0.5 tabular-nums">{formatPercent(data.confidence)}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">Risk score</dt>
                  <dd className="pt-0.5 tabular-nums">
                    {data.risk_score !== null ? data.risk_score.toFixed(0) : "—"}
                  </dd>
                </div>
                {data.measured_value !== null ? (
                  <div>
                    <dt className="text-xs uppercase tracking-wide text-muted-foreground">Measured</dt>
                    <dd className="pt-0.5 tabular-nums">
                      {data.measured_value}
                      {data.threshold_value !== null ? (
                        <span className="text-muted-foreground"> (threshold {data.threshold_value})</span>
                      ) : null}
                    </dd>
                  </div>
                ) : null}
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">Detected</dt>
                  <dd className="pt-0.5">{formatDateTime(data.detected_at)}</dd>
                </div>
              </dl>
            </CardContent>
          </Card>

          {data.evidence && Object.keys(data.evidence).length > 0 ? (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Evidence</CardTitle>
                <CardDescription>The values the rule compared, as recorded</CardDescription>
              </CardHeader>
              <CardContent>
                <pre className="overflow-x-auto rounded-md bg-muted/60 p-3 text-xs leading-relaxed">
                  {JSON.stringify(data.evidence, null, 2)}
                </pre>
              </CardContent>
            </Card>
          ) : null}

          {data.resolution_notes ? (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">Resolution</CardTitle>
                <CardDescription>
                  {data.resolved_at ? `Closed ${formatDateTime(data.resolved_at)}` : "In progress"}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <p className="whitespace-pre-wrap text-sm">{data.resolution_notes}</p>
              </CardContent>
            </Card>
          ) : null}
        </div>

        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Handling</CardTitle>
              <CardDescription>
                {data.assigned_to
                  ? `Assigned ${data.assigned_at ? formatDateTime(data.assigned_at) : ""}`
                  : "Unassigned"}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {!data.assigned_to ? (
                <Button
                  variant="outline"
                  className="w-full"
                  onClick={() => assign.mutate()}
                  loading={assign.isPending}
                >
                  <UserCheck /> Assign to me
                </Button>
              ) : null}

              {closed ? (
                <p className="flex items-start gap-2 rounded-md bg-muted/60 p-3 text-sm text-muted-foreground">
                  <ShieldCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
                  This alert is closed. A fresh scan will reopen it only if the underlying
                  condition still holds.
                </p>
              ) : (
                <form
                  onSubmit={form.handleSubmit((v) => decide.mutate(v))}
                  className="space-y-4"
                  noValidate
                >
                  <Field label="Decision" htmlFor="status" required>
                    <Select
                      value={form.watch("status")}
                      onValueChange={(v) => form.setValue("status", v as AlertDecisionValues["status"])}
                    >
                      <SelectTrigger id="status">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {ALERT_STATUSES.filter((s) => s.value !== "OPEN").map((s) => (
                          <SelectItem key={s.value} value={s.value}>
                            {s.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>

                  <Field
                    label="Notes"
                    htmlFor="resolution_notes"
                    hint="Recorded against the alert and written to the audit log."
                    error={form.formState.errors.resolution_notes?.message}
                  >
                    <Textarea
                      {...form.register("resolution_notes")}
                      {...fieldProps(
                        "resolution_notes",
                        form.formState.errors.resolution_notes?.message,
                      )}
                      rows={4}
                      placeholder="What was checked, and against what"
                    />
                  </Field>

                  <label className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4 rounded border-input"
                      {...form.register("is_false_positive")}
                    />
                    <span>
                      False positive
                      <span className="block text-xs text-muted-foreground">
                        The rule fired on sound data. Flagging it keeps the rule&apos;s precision
                        measurable.
                      </span>
                    </span>
                  </label>

                  <Button type="submit" className="w-full" loading={decide.isPending}>
                    <Gavel /> Record decision
                  </Button>
                </form>
              )}
            </CardContent>
          </Card>

          {data.short_code ? (
            <Card>
              <CardContent className="space-y-2 p-4">
                <p className="text-sm font-medium">Next steps</p>
                <Button variant="outline" size="sm" className="w-full" asChild>
                  <Link href={`/verification?ulpin=${encodeURIComponent(data.short_code)}`}>
                    Verify this identifier
                  </Link>
                </Button>
                <Button variant="outline" size="sm" className="w-full" asChild>
                  <Link href={`/search?q=${encodeURIComponent(data.short_code)}`}>
                    Find the property
                  </Link>
                </Button>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
