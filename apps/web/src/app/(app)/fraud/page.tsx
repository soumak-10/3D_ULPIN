"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RadarIcon, RefreshCw, ShieldCheck, TriangleAlert } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import {
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  SeverityBadge,
  UlpinChip,
} from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { errorMessage } from "@/lib/api/client";
import { fraud } from "@/lib/api/endpoints";
import { ALERT_SEVERITIES, ALERT_STATUSES, FRAUD_RULES } from "@/lib/constants";
import { cn, formatDateTime, formatNumber } from "@/lib/utils";

/**
 * The fraud alert dashboard (Request J).
 *
 * The queue defaults to open alerts, because an officer opening this page is
 * asking "what needs me", not "what has ever been flagged". Severity is a
 * property of the rule, not of the alert — so the rule tiles double as the
 * legend and there is no per-alert severity control anywhere in the UI.
 */

const ANY = "__any";

const RULE_COPY: Record<string, string> = {
  MULTIPLE_OWNERS: "Two or more live ownership records over the same unit.",
  DUPLICATE_ULPIN: "One identifier resolving to more than one property.",
  OWNERSHIP_MISMATCH: "The claimed owner is not the recorded owner.",
  TENANT_MISMATCH: "The claimed tenant is not the recorded tenant.",
  UNAUTHORIZED_OCCUPANCY: "Occupied, with neither a live tenancy nor a resident owner.",
};

export default function FraudPage() {
  const router = useRouter();
  const sp = useSearchParams();
  const qc = useQueryClient();

  const page = Number(sp.get("page") ?? 1) || 1;
  const pageSize = Number(sp.get("page_size") ?? 20) || 20;
  const severity = sp.get("severity") ?? undefined;
  const status = sp.get("status") ?? "OPEN";
  const ruleCode = sp.get("rule_code") ?? undefined;
  const q = sp.get("q") ?? "";

  const [draft, setDraft] = React.useState(q);
  React.useEffect(() => setDraft(q), [q]);

  const push = React.useCallback(
    (next: Record<string, string | number | undefined>) => {
      const params = new URLSearchParams(sp.toString());
      for (const [key, value] of Object.entries(next)) {
        if (value === undefined || value === "") params.delete(key);
        else params.set(key, String(value));
      }
      if (!("page" in next)) params.delete("page");
      router.push(`/fraud?${params.toString()}`, { scroll: false });
    },
    [router, sp],
  );

  const summary = useQuery({ queryKey: ["fraud-summary"], queryFn: () => fraud.summary() });

  const alerts = useQuery({
    queryKey: ["fraud-alerts", { page, pageSize, severity, status, ruleCode, q }],
    queryFn: () =>
      fraud.alerts({
        page,
        page_size: pageSize,
        severity,
        status: status === ANY ? undefined : status,
        rule_code: ruleCode,
        q: q || undefined,
      }),
    placeholderData: (prev) => prev,
  });

  const scan = useMutation({
    mutationFn: () => fraud.scan({ dry_run: false }),
    onSuccess: (r) => {
      toast.success(
        r.alerts_created === 0 && r.alerts_updated === 0
          ? `Scanned ${formatNumber(r.scanned_units)} units — nothing new.`
          : `${r.alerts_created} new, ${r.alerts_updated} updated across ${formatNumber(r.scanned_units)} units.`,
        { description: `${r.rules_run.length} rules in ${r.took_ms} ms` },
      );
      void qc.invalidateQueries({ queryKey: ["fraud-alerts"] });
      void qc.invalidateQueries({ queryKey: ["fraud-summary"] });
      void qc.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const totalPages = alerts.data
    ? Math.max(1, Math.ceil(alerts.data.total / alerts.data.page_size))
    : 1;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Fraud alerts"
        description="Findings from the rule engine. Five rules, fixed severity, one alert per distinct finding."
      >
        <Button variant="outline" size="sm" onClick={() => void alerts.refetch()} loading={alerts.isRefetching}>
          <RefreshCw /> Refresh
        </Button>
        <Button size="sm" onClick={() => scan.mutate()} loading={scan.isPending}>
          <RadarIcon /> Run scan
        </Button>
      </PageHeader>

      {/* Severity counts */}
      <section aria-label="Open alerts by severity" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {summary.isLoading
          ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20" />)
          : ALERT_SEVERITIES.map((s) => {
              const count = summary.data?.by_severity?.[s.value] ?? 0;
              const active = severity === s.value;
              return (
                <button
                  key={s.value}
                  type="button"
                  onClick={() => push({ severity: active ? undefined : s.value })}
                  aria-pressed={active}
                  className={cn(
                    "rounded-lg border p-4 text-left transition-colors hover:bg-accent/50",
                    active && "border-primary bg-accent/60 ring-1 ring-primary/30",
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <SeverityBadge severity={s.value} />
                    <span className="text-2xl font-semibold tabular-nums">{formatNumber(count)}</span>
                  </div>
                  <p className="pt-1 text-xs text-muted-foreground">
                    {count === 1 ? "open alert" : "open alerts"}
                  </p>
                </button>
              );
            })}
      </section>

      {/* Rule tiles */}
      <section aria-label="Rules" className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {summary.isLoading
          ? Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-24" />)
          : (summary.data?.rules ?? []).map((rule) => {
              const active = ruleCode === rule.rule_code;
              return (
                <Card
                  key={rule.rule_code}
                  role="button"
                  tabIndex={0}
                  aria-pressed={active}
                  onClick={() => push({ rule_code: active ? undefined : rule.rule_code })}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      push({ rule_code: active ? undefined : rule.rule_code });
                    }
                  }}
                  className={cn(
                    "cursor-pointer transition-colors hover:border-primary/40",
                    active && "border-primary ring-1 ring-primary/30",
                  )}
                >
                  <CardContent className="space-y-1.5 p-4">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-sm font-semibold">{rule.title}</p>
                      <SeverityBadge severity={rule.severity} />
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {RULE_COPY[rule.rule_code] ?? rule.description}
                    </p>
                    <p className="pt-0.5 text-xs tabular-nums text-muted-foreground">
                      <span className="font-semibold text-foreground">{rule.open_count}</span> open
                      {" · "}
                      {rule.total_count} ever
                    </p>
                  </CardContent>
                </Card>
              );
            })}
      </section>

      {/* Queue */}
      <Card>
        <CardHeader className="gap-3 pb-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle>Alert queue</CardTitle>
              <CardDescription>
                {status === ANY ? "All alerts" : `${status.toLowerCase()} alerts`}
                {ruleCode ? ` · ${ruleCode.replace(/_/g, " ").toLowerCase()}` : ""}
                {severity ? ` · ${severity.toLowerCase()} severity` : ""}
              </CardDescription>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  push({ q: draft.trim() || undefined });
                }}
              >
                <Input
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Filter by title or ULPIN"
                  className="w-full sm:w-56"
                  aria-label="Filter alerts"
                />
              </form>
              <Select value={status} onValueChange={(v) => push({ status: v })}>
                <SelectTrigger className="w-40" aria-label="Status">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>All statuses</SelectItem>
                  {ALERT_STATUSES.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={ruleCode ?? ANY}
                onValueChange={(v) => push({ rule_code: v === ANY ? undefined : v })}
              >
                <SelectTrigger className="w-48" aria-label="Rule">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ANY}>All rules</SelectItem>
                  {FRAUD_RULES.map((r) => (
                    <SelectItem key={r.value} value={r.value}>
                      {r.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardHeader>

        <CardContent className="space-y-4 pt-0">
          {alerts.isError ? (
            <ErrorState error={alerts.error} onRetry={() => void alerts.refetch()} />
          ) : alerts.isLoading ? (
            <Skeleton className="h-64" />
          ) : (alerts.data?.items.length ?? 0) === 0 ? (
            <EmptyState
              icon={ShieldCheck}
              title={status === "OPEN" ? "Nothing needs attention" : "No alerts match"}
              description={
                status === "OPEN"
                  ? "No open alerts. Run a scan if properties have changed since the last one."
                  : "Widen the filters, or run a scan to re-evaluate every unit against all five rules."
              }
            />
          ) : (
            <>
              <div className={cn("overflow-x-auto", alerts.isFetching && "opacity-60")}>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Finding</TableHead>
                      <TableHead>Property</TableHead>
                      <TableHead>Severity</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Risk</TableHead>
                      <TableHead>Detected</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {alerts.data?.items.map((a) => (
                      <TableRow key={a.alert_id}>
                        <TableCell className="max-w-sm">
                          <Link href={`/fraud/${a.alert_id}`} className="font-medium hover:underline">
                            {a.title}
                          </Link>
                          <p className="truncate text-xs text-muted-foreground">
                            {a.rule_code.replace(/_/g, " ").toLowerCase()}
                            {a.is_false_positive ? " · marked false positive" : ""}
                          </p>
                        </TableCell>
                        <TableCell className="space-y-1">
                          <UlpinChip code={a.short_code} />
                          {a.building_name ? (
                            <p className="truncate text-xs text-muted-foreground">
                              {a.building_name}
                              {a.unit_number ? ` · ${a.unit_number}` : ""}
                            </p>
                          ) : null}
                        </TableCell>
                        <TableCell>
                          <SeverityBadge severity={a.severity} />
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline">{a.status.toLowerCase()}</Badge>
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          {a.risk_score !== null ? a.risk_score.toFixed(0) : "—"}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-muted-foreground">
                          {formatDateTime(a.detected_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <Pagination
                page={alerts.data!.page}
                pageSize={alerts.data!.page_size}
                total={alerts.data!.total}
                pages={totalPages}
                onPageChange={(p) => push({ page: p })}
                onPageSizeChange={(s) => push({ page_size: s, page: undefined })}
              />
            </>
          )}
        </CardContent>
      </Card>

      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        An alert is a finding, not a determination. Each is fingerprinted, so re-running a scan
        updates the existing alert rather than raising a second copy of the same problem.
      </p>
    </div>
  );
}
