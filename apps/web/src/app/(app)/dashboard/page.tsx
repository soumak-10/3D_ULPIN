"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Building2,
  Fingerprint,
  Home,
  RefreshCw,
  ShieldCheck,
  ShieldQuestion,
  TriangleAlert,
  Users,
} from "lucide-react";
import * as React from "react";

import {
  FraudTrendChart,
  OccupancyTrendChart,
  PropertyDistributionChart,
  VerificationStatusChart,
} from "@/components/dashboard/charts";
import { UlpinGenerateCard } from "@/components/dashboard/ulpin-generate-card";
import { UlpinQrCard } from "@/components/dashboard/ulpin-qr-card";
import {
  EmptyState,
  ErrorState,
  PageHeader,
  SeverityBadge,
  StatusBadge,
  StatusLegend,
  UlpinChip,
} from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { dashboard } from "@/lib/api/endpoints";
import { cn, formatDateTime, formatNumber, formatPercent } from "@/lib/utils";
import type { StatCard } from "@/types/api";

const CARD_ICONS: Record<string, React.ElementType> = {
  total_buildings: Building2,
  total_units: Home,
  total_ulpins: Fingerprint,
  verified_properties: ShieldCheck,
  pending_properties: ShieldQuestion,
  fraud_alerts: TriangleAlert,
  active_tenants: Users,
};

const TONE_ACCENT: Record<string, string> = {
  verified: "text-verified",
  pending: "text-pending",
  fraud: "text-fraud",
  neutral: "text-muted-foreground",
  default: "text-primary",
};

function StatTile({ card }: { card: StatCard }) {
  const Icon = CARD_ICONS[card.key] ?? Home;
  const accent = TONE_ACCENT[card.tone] ?? TONE_ACCENT.default;

  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-3 p-4">
        <div className="min-w-0 space-y-1">
          <p className="truncate text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {card.label}
          </p>
          <p className="text-2xl font-semibold tabular-nums">{formatNumber(card.value)}</p>
          {card.delta_label ? (
            <p className="text-xs text-muted-foreground">
              {card.delta !== null && card.delta !== 0 ? (
                <span
                  className={cn(
                    "font-medium",
                    card.delta > 0
                      ? card.tone === "fraud"
                        ? "text-fraud"
                        : "text-verified"
                      : "text-muted-foreground",
                  )}
                >
                  {card.delta > 0 ? "+" : ""}
                  {formatNumber(card.delta)}{" "}
                </span>
              ) : null}
              {card.delta_label}
            </p>
          ) : card.hint ? (
            <p className="text-xs text-muted-foreground">{card.hint}</p>
          ) : null}
        </div>
        <span className={cn("shrink-0 rounded-md bg-muted p-2", accent)}>
          <Icon className="size-4" aria-hidden />
        </span>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
  const [days, setDays] = React.useState(30);

  const { data, isLoading, isError, error, refetch, isRefetching } = useQuery({
    queryKey: ["dashboard", days],
    queryFn: () => dashboard.overview(days),
  });

  if (isError) {
    return (
      <div className="space-y-6">
        <PageHeader title="Dashboard" />
        <ErrorState error={error} onRetry={() => void refetch()} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Register-wide position across buildings, identifiers, verification and fraud."
      >
        <div className="flex items-center gap-1 rounded-md border p-0.5">
          {[7, 30, 90].map((d) => (
            <Button
              key={d}
              size="sm"
              variant={days === d ? "secondary" : "ghost"}
              onClick={() => setDays(d)}
            >
              {d}d
            </Button>
          ))}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void refetch()}
          loading={isRefetching}
          aria-label="Refresh"
        >
          <RefreshCw /> Refresh
        </Button>
      </PageHeader>

      {/* Seven statistics cards */}
      <section
        aria-label="Key figures"
        className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7"
      >
        {isLoading
          ? Array.from({ length: 7 }).map((_, i) => (
              <Card key={i}>
                <CardContent className="space-y-2 p-4">
                  <Skeleton className="h-3 w-24" />
                  <Skeleton className="h-7 w-16" />
                  <Skeleton className="h-3 w-20" />
                </CardContent>
              </Card>
            ))
          : data?.cards.map((card) => <StatTile key={card.key} card={card} />)}
      </section>

      <StatusLegend />

      {/* Register a property → mint its 3D ULPINs + QR codes */}
      <UlpinGenerateCard />

      {/* ULPIN + QR: identifier and scannable record for a chosen unit */}
      <UlpinQrCard />

      {/* Four charts */}
      <section aria-label="Charts" className="grid gap-4 xl:grid-cols-2">
        {isLoading ? (
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-[22rem]" />)
        ) : (
          <>
            <PropertyDistributionChart data={data?.property_distribution ?? []} />
            <VerificationStatusChart data={data?.verification_status ?? []} />
            <FraudTrendChart data={data?.fraud_trend ?? []} />
            <OccupancyTrendChart data={data?.occupancy_trend ?? []} />
          </>
        )}
      </section>

      {/* Three tables */}
      <section aria-label="Recent records" className="grid gap-4 xl:grid-cols-2">
        <Card className="xl:col-span-2">
          <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
            <div className="space-y-1">
              <CardTitle>Fraud alerts</CardTitle>
              <CardDescription>Most recent findings from the rules engine</CardDescription>
            </div>
            <Button variant="outline" size="sm" asChild>
              <Link href="/fraud">Open queue</Link>
            </Button>
          </CardHeader>
          <CardContent className="pt-0">
            {isLoading ? (
              <Skeleton className="h-40" />
            ) : (data?.recent_alerts?.length ?? 0) === 0 ? (
              <EmptyState
                icon={ShieldCheck}
                title="No alerts raised"
                description="The rules engine has not flagged anything in this period."
              />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Rule</TableHead>
                    <TableHead>ULPIN</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Detected</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data?.recent_alerts?.map((row) => (
                    <TableRow key={row.alert_id}>
                      <TableCell className="font-medium">
                        <Link href={`/fraud/${row.alert_id}`} className="hover:underline">
                          {row.title}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <UlpinChip code={row.short_code} />
                      </TableCell>
                      <TableCell>
                        <SeverityBadge severity={row.severity} />
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{row.status.toLowerCase()}</Badge>
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {formatDateTime(row.detected_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
            <div className="space-y-1">
              <CardTitle>Recent verifications</CardTitle>
              <CardDescription>Latest runs of the verification engine</CardDescription>
            </div>
            <Button variant="outline" size="sm" asChild>
              <Link href="/verification">Verify</Link>
            </Button>
          </CardHeader>
          <CardContent className="pt-0">
            {isLoading ? (
              <Skeleton className="h-40" />
            ) : (data?.recent_verifications.length ?? 0) === 0 ? (
              <EmptyState title="Nothing verified yet" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>ULPIN</TableHead>
                    <TableHead>Outcome</TableHead>
                    <TableHead>Confidence</TableHead>
                    <TableHead>When</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data?.recent_verifications.map((row) => (
                    <TableRow key={row.verification_id}>
                      <TableCell>
                        <UlpinChip code={row.ulpin_code} />
                      </TableCell>
                      <TableCell>
                        <StatusBadge outcome={row.outcome} />
                      </TableCell>
                      <TableCell className="tabular-nums text-muted-foreground">
                        {formatPercent(row.confidence)}
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

        <Card>
          <CardHeader className="pb-3">
            <CardTitle>Recent activity</CardTitle>
            <CardDescription>
              From the append-only audit log — hash-chained, so a deleted row is detectable
            </CardDescription>
          </CardHeader>
          <CardContent className="pt-0">
            {isLoading ? (
              <Skeleton className="h-40" />
            ) : (data?.recent_activities.length ?? 0) === 0 ? (
              <EmptyState title="No recorded activity" />
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Action</TableHead>
                    <TableHead>Entity</TableHead>
                    <TableHead>By</TableHead>
                    <TableHead>When</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data?.recent_activities.map((row) => (
                    <TableRow key={row.log_id}>
                      <TableCell className="font-medium">
                        <span className="flex items-center gap-1.5">
                          <span
                            className={cn(
                              "size-1.5 shrink-0 rounded-full",
                              row.success ? "bg-verified" : "bg-fraud",
                            )}
                            aria-hidden
                          />
                          {row.action.replace(/_/g, " ").toLowerCase()}
                        </span>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {row.ulpin_code ? (
                          <span className="ulpin text-xs">{row.ulpin_code}</span>
                        ) : (
                          (row.entity_type?.toLowerCase() ?? "—")
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {row.actor_role?.replace(/_/g, " ").toLowerCase() ?? "system"}
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-muted-foreground">
                        {formatDateTime(row.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </section>

      {data ? (
        <p className="text-xs text-muted-foreground">
          Generated {formatDateTime(data.generated_at)}. Counts are of properties, not of
          records — a unit verified four times is counted once.
        </p>
      ) : null}
    </div>
  );
}
