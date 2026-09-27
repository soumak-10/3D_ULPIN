"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Box, ShieldCheck, Users } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import {
  ErrorState,
  PageHeader,
  StatusBadge,
  UlpinChip,
} from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { buildings, fraud, units } from "@/lib/api/endpoints";
import { OCCUPANCY_STATUSES } from "@/lib/constants";
import { formatDate, formatNumber } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

/**
 * One unit, in full (Request E / L).
 *
 * This is where a search result lands, so it has to answer the question that
 * sent the user here — who holds this, who lives in it, is it sound — without a
 * second navigation. Ownership, tenancy and alerts are therefore fetched
 * alongside the unit rather than hidden behind tabs.
 */

/** `units.ownership` is typed `unknown[]` at the client: the ownership row is
 *  assembled by the API from two tables and has no single schema on this side. */
interface OwnershipRow {
  ownership_id?: string;
  owner_id?: string;
  owner_name?: string;
  display_name?: string;
  ownership_mode?: string;
  share_fraction?: number;
  is_primary?: boolean;
  acquired_on?: string;
  deed_number?: string | null;
  status?: string;
}

export default function UnitDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { isStaff } = useAuth();
  const unitId = params.id;

  const unit = useQuery({
    queryKey: ["unit", unitId],
    queryFn: () => units.get(unitId),
  });

  const ownership = useQuery({
    queryKey: ["unit-ownership", unitId],
    queryFn: () => units.ownership(unitId) as Promise<OwnershipRow[]>,
  });

  const tenancies = useQuery({
    queryKey: ["unit-tenancies", unitId],
    queryFn: () => units.tenancies(unitId),
  });

  const building = useQuery({
    queryKey: ["building", unit.data?.building_id],
    queryFn: () => buildings.get(unit.data!.building_id),
    enabled: Boolean(unit.data?.building_id),
  });

  // `GET /fraud/alerts` filters by building, not by unit, so the building's
  // open alerts are fetched and narrowed below. The key matches the viewer's,
  // so arriving here from the model costs nothing.
  const alerts = useQuery({
    queryKey: ["building-alerts", unit.data?.building_id],
    queryFn: () =>
      fraud.alerts({ building_id: unit.data!.building_id, status: "OPEN", page_size: 200 }),
    enabled: Boolean(unit.data?.building_id),
  });

  const openAlerts = React.useMemo(
    () => (alerts.data?.items ?? []).filter((a) => a.unit_id === unitId),
    [alerts.data, unitId],
  );
  const firstAlert = openAlerts[0] ?? null;

  const setOccupancy = useMutation({
    mutationFn: (status: string) => units.setOccupancy(unitId, status),
    onSuccess: (u) => {
      toast.success(`Marked ${u.occupancy_status.replace(/_/g, " ").toLowerCase()}.`);
      void qc.invalidateQueries({ queryKey: ["unit", unitId] });
      void qc.invalidateQueries({ queryKey: ["building-units"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (unit.isError) {
    return <ErrorState error={unit.error} onRetry={() => void unit.refetch()} />;
  }

  if (unit.isLoading || !unit.data) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-72" />
      </div>
    );
  }

  const u = unit.data;
  const code = u.short_code ?? u.ulpin_code;
  const activeTenant = (tenancies.data ?? []).find((t) => t.status === "ACTIVE") ?? null;

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" className="-ml-2" onClick={() => router.back()}>
        <ArrowLeft /> Back
      </Button>

      <PageHeader
        title={`Unit ${u.unit_number}`}
        description={
          building.data
            ? `${building.data.building_name} · floor ${u.floor_number} · ${u.property_type
                .replace(/_/g, " ")
                .toLowerCase()}`
            : `Floor ${u.floor_number}`
        }
      >
        <div className="flex flex-wrap gap-2">
          {code ? (
            <Button variant="outline" asChild>
              <Link href={`/verification?ulpin=${encodeURIComponent(code)}`}>
                <ShieldCheck /> Verify
              </Link>
            </Button>
          ) : null}
          <Button variant="outline" asChild>
            <Link href={`/viewer?building=${u.building_id}`}>
              <Box /> View in 3D
            </Link>
          </Button>
        </div>
      </PageHeader>

      {firstAlert ? (
        <Card className="border-fraud/40 bg-fraud/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4">
            <p className="text-sm">
              <span className="font-medium">
                {openAlerts.length} open {openAlerts.length === 1 ? "alert" : "alerts"}
              </span>{" "}
              <span className="text-muted-foreground">
                on this unit. An alert is a finding awaiting an officer&apos;s decision, not a
                determination.
              </span>
            </p>
            <Button size="sm" variant="outline" asChild>
              <Link href={`/fraud/${firstAlert.alert_id}`}>Open the alert</Link>
            </Button>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="space-y-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Users className="size-4" aria-hidden /> Ownership
              </CardTitle>
              <CardDescription>
                Shares are recorded as fractions of the whole. They should sum to one.
              </CardDescription>
            </CardHeader>
            <CardContent className="pt-0">
              {ownership.isLoading ? (
                <Skeleton className="h-24" />
              ) : (ownership.data?.length ?? 0) === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  No ownership recorded. Record it from the building page.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Owner</TableHead>
                        <TableHead>Mode</TableHead>
                        <TableHead className="text-right">Share</TableHead>
                        <TableHead>Acquired</TableHead>
                        <TableHead>Deed</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {ownership.data?.map((o, i) => (
                        <TableRow key={o.ownership_id ?? o.owner_id ?? i}>
                          <TableCell className="font-medium">
                            {o.owner_name ?? o.display_name ?? "—"}
                            {o.is_primary ? (
                              <Badge variant="outline" className="ml-2 text-[10px]">
                                primary
                              </Badge>
                            ) : null}
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            {(o.ownership_mode ?? "—").toLowerCase()}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {o.share_fraction !== undefined
                              ? `${Math.round(o.share_fraction * 100)}%`
                              : "—"}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-muted-foreground">
                            {o.acquired_on ? formatDate(o.acquired_on) : "—"}
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            {o.deed_number ?? "—"}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Tenancy</CardTitle>
              <CardDescription>Current and past occupants of record.</CardDescription>
            </CardHeader>
            <CardContent className="pt-0">
              {tenancies.isLoading ? (
                <Skeleton className="h-20" />
              ) : (tenancies.data?.length ?? 0) === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  No tenancy recorded.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Tenant</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Lease start</TableHead>
                        <TableHead>Lease end</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {tenancies.data?.map((t) => (
                        <TableRow key={t.tenant_id}>
                          <TableCell className="font-medium">{t.full_name}</TableCell>
                          <TableCell>
                            <Badge variant={t.status === "ACTIVE" ? "verified" : "outline"}>
                              {t.status.toLowerCase()}
                            </Badge>
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-muted-foreground">
                            {t.lease_start ? formatDate(t.lease_start) : "—"}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-muted-foreground">
                            {t.lease_end ? formatDate(t.lease_end) : "—"}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        <Card className="h-fit">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Record</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge outcome={u.verification_outcome} openAlerts={openAlerts.length} />
              <Badge variant="outline">
                {u.occupancy_status.replace(/_/g, " ").toLowerCase()}
              </Badge>
            </div>

            <dl className="space-y-3 text-sm">
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">ULPIN</dt>
                <dd className="pt-1">
                  {code ? <UlpinChip code={code} /> : <span className="text-muted-foreground">not issued</span>}
                </dd>
              </div>
              {u.ulpin_code && u.short_code ? (
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                    Parcel code
                  </dt>
                  <dd className="ulpin pt-0.5 text-xs">{u.ulpin_code}</dd>
                </div>
              ) : null}
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Building</dt>
                <dd className="pt-0.5">
                  <Link href={`/buildings/${u.building_id}`} className="hover:underline">
                    {building.data?.building_name ?? "—"}
                  </Link>
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  Floor number
                </dt>
                <dd className="pt-0.5 tabular-nums">{u.floor_number}</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Owner</dt>
                <dd className="pt-0.5">
                  {(ownership.data ?? [])
                    .map((o) => o.owner_name ?? o.display_name)
                    .filter(Boolean)
                    .join(", ") || "not recorded"}
                </dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">Tenant</dt>
                <dd className="pt-0.5">{activeTenant?.full_name ?? "none"}</dd>
              </div>
              {u.carpet_area_sqm !== null ? (
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                    Carpet area
                  </dt>
                  <dd className="pt-0.5 tabular-nums">{formatNumber(u.carpet_area_sqm)} m²</dd>
                </div>
              ) : null}
              {u.volume_cum !== null ? (
                <div>
                  <dt className="text-xs uppercase tracking-wide text-muted-foreground">Volume</dt>
                  <dd className="pt-0.5 tabular-nums">
                    {formatNumber(u.volume_cum)} m³
                    {u.is_solid_valid === false ? (
                      <span className="pl-1 text-xs text-pending">(geometry not closed)</span>
                    ) : null}
                  </dd>
                </div>
              ) : null}
              <div>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  Last verified
                </dt>
                <dd className="pt-0.5">
                  {u.last_verified_at ? formatDate(u.last_verified_at) : "never"}
                </dd>
              </div>
            </dl>

            {isStaff ? (
              <div className="space-y-1.5 border-t pt-3">
                <label
                  htmlFor="occupancy"
                  className="text-xs uppercase tracking-wide text-muted-foreground"
                >
                  Change occupancy
                </label>
                <Select
                  value={u.occupancy_status}
                  onValueChange={(v) => setOccupancy.mutate(v)}
                  disabled={setOccupancy.isPending}
                >
                  <SelectTrigger id="occupancy">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {OCCUPANCY_STATUSES.map((s) => (
                      <SelectItem key={s.value} value={s.value}>
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
