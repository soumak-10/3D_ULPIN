"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Box, Building2, Expand, Minimize, MousePointerClick, X } from "lucide-react";
import * as React from "react";

import {
  EmptyState,
  ErrorState,
  PageHeader,
  StatusBadge,
  StatusLegend,
  UlpinChip,
} from "@/components/shared";
import type { SceneUnit } from "@/components/viewer/building-scene";
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
import { buildings, fraud } from "@/lib/api/endpoints";
import { formatDate, formatNumber } from "@/lib/utils";

/**
 * The 3D property viewer (Request G).
 *
 * The scene is loaded client-side only: Three.js touches `window` on import and
 * there is nothing meaningful to server-render for a WebGL canvas anyway.
 *
 * There is no viewer-model endpoint on the API by design — the model is composed
 * from the same building, floor and unit records every other page reads, so a
 * unit can never be one colour in the search results and another in the model.
 */
const BuildingScene = dynamic(
  () => import("@/components/viewer/building-scene").then((m) => m.BuildingScene),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full items-center justify-center bg-muted/40">
        <p className="text-sm text-muted-foreground">Preparing the model…</p>
      </div>
    ),
  },
);

export default function ViewerPage() {
  const router = useRouter();
  const sp = useSearchParams();
  const buildingId = sp.get("building") ?? undefined;

  const [selected, setSelected] = React.useState<string | null>(null);
  const [fullscreen, setFullscreen] = React.useState(false);
  const shellRef = React.useRef<HTMLDivElement>(null);

  const list = useQuery({
    queryKey: ["buildings", "viewer-picker"],
    queryFn: () => buildings.list({ page: 1, page_size: 100 }),
  });

  // Fall back to the first building so the page is never an empty canvas with a
  // dropdown the user has to discover.
  const activeId = buildingId ?? list.data?.items[0]?.building_id;

  const building = useQuery({
    queryKey: ["building", activeId],
    queryFn: () => buildings.get(activeId!),
    enabled: Boolean(activeId),
  });

  const floors = useQuery({
    queryKey: ["building-floors", activeId],
    queryFn: () => buildings.floors(activeId!),
    enabled: Boolean(activeId),
  });

  const units = useQuery({
    queryKey: ["building-units", activeId],
    queryFn: () => buildings.units(activeId!, { page: 1, page_size: 500 }),
    enabled: Boolean(activeId),
  });

  // Open alerts decide the red, so they are fetched alongside rather than
  // inferred from the verification outcome.
  const alerts = useQuery({
    queryKey: ["building-alerts", activeId],
    queryFn: () => fraud.alerts({ building_id: activeId, status: "OPEN", page_size: 200 }),
    enabled: Boolean(activeId),
  });

  const alertsByUnit = React.useMemo(() => {
    const map = new Map<string, number>();
    for (const a of alerts.data?.items ?? []) {
      if (!a.unit_id) continue;
      map.set(a.unit_id, (map.get(a.unit_id) ?? 0) + 1);
    }
    return map;
  }, [alerts.data]);

  const sceneUnits = React.useMemo<SceneUnit[]>(() => {
    const floorById = new Map((floors.data ?? []).map((f) => [f.floor_id, f]));
    return (units.data?.items ?? [])
      .map((unit) => {
        const floor = floorById.get(unit.floor_id);
        if (!floor) return null;
        return { unit, floor, openAlerts: alertsByUnit.get(unit.unit_id) ?? 0 };
      })
      .filter((x): x is SceneUnit => x !== null);
  }, [units.data, floors.data, alertsByUnit]);

  const selectedEntry = sceneUnits.find((u) => u.unit.unit_id === selected) ?? null;

  const tenancies = useQuery({
    queryKey: ["unit-tenancies", selected],
    queryFn: () => import("@/lib/api/endpoints").then((m) => m.units.tenancies(selected!)),
    enabled: Boolean(selected),
  });

  const toggleFullscreen = React.useCallback(async () => {
    const el = shellRef.current;
    if (!el) return;
    if (document.fullscreenElement) await document.exitFullscreen();
    else await el.requestFullscreen();
  }, []);

  React.useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  React.useEffect(() => setSelected(null), [activeId]);

  const loading = building.isLoading || floors.isLoading || units.isLoading;
  const error = building.error ?? floors.error ?? units.error;

  return (
    <div className="space-y-6">
      <PageHeader
        title="3D viewer"
        description="Each volume is one unit. Click it for its identifier, owner, tenant and status."
      >
        <Select
          value={activeId ?? ""}
          onValueChange={(v) => router.push(`/viewer?building=${v}`, { scroll: false })}
        >
          <SelectTrigger className="w-64" aria-label="Building">
            <SelectValue placeholder="Choose a building" />
          </SelectTrigger>
          <SelectContent>
            {list.data?.items.map((b) => (
              <SelectItem key={b.building_id} value={b.building_id}>
                {b.building_name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </PageHeader>

      {!list.isLoading && (list.data?.items.length ?? 0) === 0 ? (
        <EmptyState
          icon={Building2}
          title="No buildings registered"
          description="Register a building and its units, and the model builds itself from those records."
        />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void building.refetch()} />
      ) : (
        <div ref={shellRef} className="grid gap-4 bg-background lg:grid-cols-[1fr_22rem]">
          <Card className="overflow-hidden">
            <div className="relative h-[34rem] w-full lg:h-[38rem]">
              {loading ? (
                <Skeleton className="h-full w-full rounded-none" />
              ) : sceneUnits.length === 0 ? (
                <div className="flex h-full items-center justify-center">
                  <EmptyState
                    icon={Box}
                    title="This building has no units yet"
                    description="Add floors and units, and they appear here immediately."
                  />
                </div>
              ) : (
                <BuildingScene
                  units={sceneUnits}
                  selectedId={selected}
                  onSelect={setSelected}
                  className="h-full w-full"
                />
              )}

              <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-3">
                <div className="pointer-events-auto rounded-md bg-background/85 px-3 py-2 shadow-sm backdrop-blur">
                  <p className="text-sm font-semibold">{building.data?.building_name ?? "—"}</p>
                  <p className="text-xs text-muted-foreground">
                    {building.data ? `${building.data.city}, ${building.data.state_code}` : ""}
                    {" · "}
                    {formatNumber(sceneUnits.length)} units over {floors.data?.length ?? 0} floors
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="secondary"
                  className="pointer-events-auto"
                  onClick={() => void toggleFullscreen()}
                >
                  {fullscreen ? <Minimize /> : <Expand />}
                  {fullscreen ? "Exit" : "Fullscreen"}
                </Button>
              </div>

              <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-2 p-3">
                <div className="pointer-events-auto rounded-md bg-background/85 px-3 py-2 shadow-sm backdrop-blur">
                  <StatusLegend />
                </div>
                <p className="hidden rounded-md bg-background/85 px-3 py-2 text-xs text-muted-foreground shadow-sm backdrop-blur sm:block">
                  Drag to rotate · scroll to zoom · right-drag to pan
                </p>
              </div>
            </div>
          </Card>

          <Card className="lg:max-h-[38rem] lg:overflow-y-auto">
            {selectedEntry ? (
              <>
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="space-y-1">
                      <CardTitle className="text-base">
                        Unit {selectedEntry.unit.unit_number}
                      </CardTitle>
                      <CardDescription>
                        {selectedEntry.floor.floor_label ??
                          `Floor ${selectedEntry.floor.floor_number}`}
                        {" · "}
                        {selectedEntry.unit.property_type.replace(/_/g, " ").toLowerCase()}
                      </CardDescription>
                    </div>
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => setSelected(null)}
                      aria-label="Clear selection"
                    >
                      <X />
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusBadge
                      outcome={selectedEntry.unit.verification_outcome}
                      openAlerts={selectedEntry.openAlerts}
                    />
                    <Badge variant="outline">
                      {selectedEntry.unit.occupancy_status.replace(/_/g, " ").toLowerCase()}
                    </Badge>
                  </div>

                  <dl className="space-y-3 text-sm">
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-muted-foreground">ULPIN</dt>
                      <dd className="pt-1">
                        <UlpinChip
                          code={selectedEntry.unit.short_code ?? selectedEntry.unit.ulpin_code}
                        />
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                        Floor number
                      </dt>
                      <dd className="pt-0.5 tabular-nums">{selectedEntry.floor.floor_number}</dd>
                    </div>
                    {/* The vertical mapping, stated as numbers. This is what makes
                        the identifier 3D: the ULPIN above and this position below
                        describe the same thing, and a registrar can check one
                        against the other. */}
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                        Coordinates
                      </dt>
                      <dd className="pt-0.5 tabular-nums">
                        {selectedEntry.unit.x_coordinate === null ? (
                          <span className="text-muted-foreground">
                            not placed — awaiting survey
                          </span>
                        ) : (
                          <>
                            X={selectedEntry.unit.x_coordinate} Y={selectedEntry.unit.y_coordinate}{" "}
                            Z={selectedEntry.unit.z_coordinate}
                            <span className="ml-1 text-xs text-muted-foreground">m</span>
                          </>
                        )}
                      </dd>
                    </div>
                    {selectedEntry.unit.width_m !== null ? (
                      <div>
                        <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                          Dimensions
                        </dt>
                        <dd className="pt-0.5 tabular-nums">
                          Width={selectedEntry.unit.width_m}m Length=
                          {selectedEntry.unit.length_m}m Height={selectedEntry.unit.height_m}m
                        </dd>
                      </div>
                    ) : null}
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-muted-foreground">Owner</dt>
                      <dd className="pt-0.5">
                        {/* Ownership is read per unit rather than bulk-loaded for
                            the whole tower: a 400-flat building would otherwise
                            pull 400 ownership rows to show one. */}
                        <OwnerLine unitId={selectedEntry.unit.unit_id} />
                      </dd>
                    </div>
                    <div>
                      <dt className="text-xs uppercase tracking-wide text-muted-foreground">Tenant</dt>
                      <dd className="pt-0.5">
                        {tenancies.isLoading
                          ? "…"
                          : ((tenancies.data ?? []).find((t) => t.status === "ACTIVE")?.full_name ??
                            "none")}
                      </dd>
                    </div>
                    {selectedEntry.unit.carpet_area_sqm !== null ? (
                      <div>
                        <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                          Carpet area
                        </dt>
                        <dd className="pt-0.5 tabular-nums">
                          {formatNumber(selectedEntry.unit.carpet_area_sqm)} m²
                        </dd>
                      </div>
                    ) : null}
                    {selectedEntry.unit.last_verified_at ? (
                      <div>
                        <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                          Last verified
                        </dt>
                        <dd className="pt-0.5">{formatDate(selectedEntry.unit.last_verified_at)}</dd>
                      </div>
                    ) : null}
                  </dl>

                  <div className="space-y-2 border-t pt-3">
                    {selectedEntry.unit.short_code ? (
                      <Button variant="outline" size="sm" className="w-full" asChild>
                        <Link
                          href={`/verification?ulpin=${encodeURIComponent(selectedEntry.unit.short_code)}`}
                        >
                          Verify this unit
                        </Link>
                      </Button>
                    ) : null}
                    <Button variant="ghost" size="sm" className="w-full" asChild>
                      <Link href={`/buildings/${selectedEntry.unit.building_id}`}>
                        Open the building record
                      </Link>
                    </Button>
                  </div>
                </CardContent>
              </>
            ) : (
              <CardContent className="flex h-full min-h-[18rem] items-center justify-center p-6">
                <EmptyState
                  icon={MousePointerClick}
                  title="Select a unit"
                  description="Click any volume in the model. Green is verified, yellow pending, red flagged or adverse."
                />
              </CardContent>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}

function OwnerLine({ unitId }: { unitId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["unit-ownership", unitId],
    queryFn: () => import("@/lib/api/endpoints").then((m) => m.units.ownership(unitId)),
  });

  if (isLoading) return <>…</>;

  const rows = (data ?? []) as Array<{ owner_name?: string; display_name?: string }>;
  const names = rows.map((r) => r.owner_name ?? r.display_name).filter(Boolean);
  return <>{names.length > 0 ? names.join(", ") : "not recorded"}</>;
}
