"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Box, Building2, Download, Fingerprint, MapPin, QrCode, User } from "lucide-react";
import QRCode from "react-qr-code";
import * as React from "react";

import { EmptyState, StatusBadge, UlpinChip } from "@/components/shared";
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
import { buildings, threeDUlpin } from "@/lib/api/endpoints";
import type { ThreeDBuildingResponse, ThreeDUnitResponse } from "@/types/api";

/**
 * Dashboard ULPIN section (Request: "a ULPIN with a QR code").
 *
 * Picks a building and one of its units, then renders that unit's identifier
 * beside a QR code. The identifier already encodes STATE-CITY-BUILDING-FLOOR-UNIT;
 * the QR carries the full record — building, owner and place — so a scan on site
 * resolves the physical unit without a network round trip. The data is the same
 * `/3d-ulpin/building/{id}` payload the 3D viewer reads, so a code here and a
 * volume there always describe the same unit.
 */

const num = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined ? "—" : v.toFixed(digits);

/** The human-readable record encoded into the QR. Labelled lines rather than
 *  JSON so any phone camera shows a legible property card, not a blob. */
function qrPayload(b: ThreeDBuildingResponse, u: ThreeDUnitResponse): string {
  const code = u.short_code ?? u.ulpin ?? "UNASSIGNED";
  const place = [b.city, b.state].filter(Boolean).join(", ") || "—";
  return [
    `ULPIN: ${code}`,
    `Building: ${b.building_name}${b.building_code ? ` (${b.building_code})` : ""}`,
    `Owner: ${u.owner_name ?? "Not recorded"}`,
    `Place: ${place}`,
    `Geo: ${num(b.latitude, 6)}, ${num(b.longitude, 6)}`,
    u.coordinates
      ? `Position: X=${num(u.coordinates.x)} Y=${num(u.coordinates.y)} Z=${num(u.coordinates.z)} m`
      : `Position: not placed`,
    `Floor ${u.floor_number ?? "—"} · Unit ${u.unit_number}`,
  ].join("\n");
}

function Row({ icon: Icon, label, children }: { icon: React.ElementType; label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5">
      <span className="mt-0.5 shrink-0 rounded-md bg-muted p-1.5 text-muted-foreground">
        <Icon className="size-3.5" aria-hidden />
      </span>
      <div className="min-w-0 space-y-0.5">
        <dt className="text-xs uppercase tracking-wide text-muted-foreground">{label}</dt>
        <dd className="text-sm">{children}</dd>
      </div>
    </div>
  );
}

export function UlpinQrCard() {
  const qrRef = React.useRef<HTMLDivElement>(null);

  const list = useQuery({
    queryKey: ["buildings", "ulpin-qr-picker"],
    queryFn: () => buildings.list({ page: 1, page_size: 100 }),
  });

  const [buildingId, setBuildingId] = React.useState<string | null>(null);
  const activeBuildingId = buildingId ?? list.data?.items[0]?.building_id ?? null;

  const detail = useQuery({
    queryKey: ["3d-building", activeBuildingId],
    queryFn: () => threeDUlpin.byBuilding(activeBuildingId!),
    enabled: Boolean(activeBuildingId),
  });

  // Prefer units that actually carry a placement — a QR for an unplaced unit is
  // missing the "place" half the record promises.
  const placed = React.useMemo(
    () => (detail.data?.units ?? []).filter((u) => u.coordinates !== null),
    [detail.data],
  );
  const candidates = placed.length > 0 ? placed : (detail.data?.units ?? []);

  const [unitId, setUnitId] = React.useState<string | null>(null);
  // Default to a unit that actually has an owner so the first QR shown carries a
  // full record; the basement/parking rows sort first but read as empty cards.
  const preferred = React.useMemo(
    () => candidates.find((u) => u.owner_name) ?? candidates[0] ?? null,
    [candidates],
  );
  const activeUnitId = unitId ?? preferred?.unit_id ?? null;
  const unit = candidates.find((u) => u.unit_id === activeUnitId) ?? preferred;

  // Reset the unit selection whenever the building changes so a stale unit from
  // the previous tower is never shown against the new one.
  React.useEffect(() => setUnitId(null), [activeBuildingId]);

  const payload = detail.data && unit ? qrPayload(detail.data, unit) : "";
  const code = unit?.short_code ?? unit?.ulpin ?? null;

  const downloadSvg = React.useCallback(() => {
    const svg = qrRef.current?.querySelector("svg");
    if (!svg) return;
    const blob = new Blob([new XMLSerializer().serializeToString(svg)], {
      type: "image/svg+xml",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${code ?? "ulpin"}.svg`;
    a.click();
    URL.revokeObjectURL(url);
  }, [code]);

  return (
    <Card>
      <CardHeader className="flex-col gap-3 space-y-0 sm:flex-row sm:items-center sm:justify-between">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2">
            <QrCode className="size-4 text-primary" aria-hidden />
            ULPIN &amp; QR code
          </CardTitle>
          <CardDescription>
            The identifier encodes state, city, building, floor and unit. The QR carries the full
            record — building, owner and place — so a scan resolves the unit on site.
          </CardDescription>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select
            value={activeBuildingId ?? ""}
            onValueChange={(v) => setBuildingId(v)}
            disabled={list.isLoading || (list.data?.items.length ?? 0) === 0}
          >
            <SelectTrigger className="w-full sm:w-48" aria-label="Building">
              <SelectValue placeholder="Building" />
            </SelectTrigger>
            <SelectContent>
              {list.data?.items.map((b) => (
                <SelectItem key={b.building_id} value={b.building_id}>
                  {b.building_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={activeUnitId ?? ""}
            onValueChange={(v) => setUnitId(v)}
            disabled={detail.isLoading || candidates.length === 0}
          >
            <SelectTrigger className="w-full sm:w-44" aria-label="Unit">
              <SelectValue placeholder="Unit" />
            </SelectTrigger>
            <SelectContent>
              {candidates.map((u) => (
                <SelectItem key={u.unit_id} value={u.unit_id}>
                  {(u.short_code ?? u.ulpin ?? u.unit_number) +
                    (u.floor_number !== null ? ` · Floor ${u.floor_number}` : "")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </CardHeader>

      <CardContent>
        {list.isLoading || detail.isLoading ? (
          <div className="grid gap-6 sm:grid-cols-[auto_1fr]">
            <Skeleton className="size-[188px] rounded-md" />
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </div>
          </div>
        ) : !detail.data || !unit ? (
          <EmptyState
            icon={Box}
            title="No unit to encode"
            description="Register a building with units, generate its 3D ULPINs, and its QR card appears here."
          />
        ) : (
          <div className="grid gap-6 sm:grid-cols-[auto_1fr]">
            {/* QR + identifier */}
            <div className="flex flex-col items-center gap-3">
              <div ref={qrRef} className="rounded-lg border bg-white p-3">
                <QRCode value={payload} size={160} level="M" aria-label={`QR code for ${code}`} />
              </div>
              {code ? <UlpinChip code={code} /> : (
                <span className="text-xs text-muted-foreground">Identifier not yet issued</span>
              )}
              <Button variant="ghost" size="sm" onClick={downloadSvg}>
                <Download /> Download QR
              </Button>
            </div>

            {/* The record the QR carries, shown as text too */}
            <div className="space-y-4">
              <dl className="grid gap-3 sm:grid-cols-2">
                <Row icon={Building2} label="Building">
                  {detail.data.building_name}
                  {detail.data.building_code ? (
                    <span className="ml-1 text-muted-foreground">({detail.data.building_code})</span>
                  ) : null}
                </Row>
                <Row icon={User} label="Owner">
                  {unit.owner_name ?? <span className="text-muted-foreground">Not recorded</span>}
                </Row>
                <Row icon={MapPin} label="Place">
                  {[detail.data.city, detail.data.state].filter(Boolean).join(", ") || "—"}
                  <div className="text-xs text-muted-foreground tabular-nums">
                    {num(detail.data.latitude, 6)}, {num(detail.data.longitude, 6)}
                  </div>
                </Row>
                <Row icon={Fingerprint} label="Floor / Unit">
                  <span className="tabular-nums">
                    Floor {unit.floor_number ?? "—"} · Unit {unit.unit_number}
                  </span>
                </Row>
                <Row icon={Box} label="Position (m)">
                  {unit.coordinates ? (
                    <span className="tabular-nums">
                      X={num(unit.coordinates.x)} Y={num(unit.coordinates.y)} Z={num(unit.coordinates.z)}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">not placed</span>
                  )}
                </Row>
                <div className="flex items-start gap-2.5">
                  <span className="mt-0.5 shrink-0 rounded-md bg-muted p-1.5 text-muted-foreground">
                    <QrCode className="size-3.5" aria-hidden />
                  </span>
                  <div className="min-w-0 space-y-1">
                    <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                      Verification
                    </dt>
                    <dd>
                      <StatusBadge outcome={unit.verification_status} />
                    </dd>
                  </div>
                </div>
              </dl>

              <div className="flex flex-wrap gap-2 border-t pt-3">
                <Button variant="outline" size="sm" asChild>
                  <Link href={`/viewer?building=${detail.data.building_id}`}>
                    <Box /> Open in 3D viewer
                  </Link>
                </Button>
                {code ? (
                  <Button variant="ghost" size="sm" asChild>
                    <Link href={`/verification?ulpin=${encodeURIComponent(code)}`}>Verify unit</Link>
                  </Button>
                ) : null}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
