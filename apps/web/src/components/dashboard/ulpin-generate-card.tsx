"use client";

/**
 * Dashboard "register property → ULPIN + QR" section.
 *
 * Request: "upload details to generate ULPIN option where users put their
 * property details and as a result they get their ULPIN and QR code."
 *
 * Two modes:
 *  - New property: the user types friendly details (name, address, city, state,
 *    a map pin, how many floors and units per floor). The technical registry
 *    fields the API insists on — the 14-char parcel ULPIN and the jurisdiction
 *    code — are derived here so the user never has to know they exist. The
 *    building is created, then its storeys and units are scaffolded and their
 *    3D ULPINs minted in one submit.
 *  - Existing building: pick one already registered and mint its ULPINs.
 *
 * On success every minted unit is shown with its own QR code. The QR carries the
 * full record (ULPIN, building, place, position) so a scan on site resolves the
 * unit without a lookup.
 */

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, Download, Layers, MapPin, QrCode, Sparkles } from "lucide-react";
import QRCode from "react-qr-code";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { EmptyState, UlpinChip } from "@/components/shared";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, fieldProps } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError, errorMessage } from "@/lib/api/client";
import { buildings, threeDUlpin } from "@/lib/api/endpoints";
import { PROPERTY_TYPES, STATE_CODES } from "@/lib/constants";
import { cn } from "@/lib/utils";
import type { ThreeDGenerateResponse, ThreeDUnitResponse } from "@/types/api";

/* --------------------------------------------------------------- validation -- */

const generateSchema = z.object({
  building_name: z.string().min(3, "Enter the building's name.").max(200),
  address_line1: z.string().min(5, "Enter the street address.").max(255),
  city: z.string().min(2, "Enter the city.").max(120),
  state_code: z.string().length(2, "Select a state."),
  // India spans roughly 6.5°–37.5°N and 68°–97.5°E. Bounding the pair stops a
  // transposed lat/long reaching the database.
  latitude: z.coerce
    .number({ invalid_type_error: "Enter a latitude." })
    .min(6, "That latitude is outside India.")
    .max(38, "That latitude is outside India."),
  longitude: z.coerce
    .number({ invalid_type_error: "Enter a longitude." })
    .min(67, "That longitude is outside India.")
    .max(98, "That longitude is outside India."),
  floors: z.coerce
    .number({ invalid_type_error: "Enter the number of floors." })
    .int("Floors are whole numbers.")
    .min(1, "A building has at least one floor.")
    .max(50, "More than 50 floors needs manual entry."),
  units_per_floor: z.coerce
    .number({ invalid_type_error: "Enter units per floor." })
    .int("Units are whole numbers.")
    .min(1, "At least one unit per floor.")
    .max(20, "More than 20 per floor needs manual entry."),
  property_type: z
    .enum([
      "RESIDENTIAL",
      "COMMERCIAL",
      "INDUSTRIAL",
      "INSTITUTIONAL",
      "MIXED_USE",
      "PARKING",
      "UTILITY",
      "COMMON_AREA",
    ])
    .default("RESIDENTIAL"),
});
type GenerateValues = z.infer<typeof generateSchema>;

/* --------------------------------------------------------- derived registry -- */

/** 3 uppercase letters from a place name, padded — e.g. "Bengaluru" → "BEN". */
function placePrefix(name: string): string {
  return (name.replace(/[^A-Za-z]/g, "").toUpperCase() + "XXX").slice(0, 3);
}

/**
 * The registry fields the API requires but a citizen should never type.
 *
 * `parcel_ulpin` must match ^[A-Z0-9]{14}$ and be unique per building; the
 * time-based tail keeps repeated submissions from colliding. `jurisdiction_code`
 * must match ^[A-Z]{2}(-[A-Z0-9]{2,6}){0,3}$.
 */
function deriveRegistry(stateCode: string, city: string) {
  const pfx = placePrefix(city);
  const tail = String(Date.now()).slice(-7); // 7 digits
  const seq = tail.slice(-3);
  return {
    parcel_ulpin: `IN${stateCode}${pfx}${tail}`.slice(0, 14),
    jurisdiction_code: `${stateCode}-${pfx}-${seq}`,
  };
}

/* ---------------------------------------------------------------- QR record -- */

const num = (v: number | string | null | undefined, digits = 2) => {
  if (v === null || v === undefined || v === "") return "—";
  const n = typeof v === "string" ? Number(v) : v;
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
};

function qrPayload(u: ThreeDUnitResponse, place: string): string {
  const code = u.short_code ?? u.ulpin ?? "UNASSIGNED";
  const c = u.coordinates;
  return [
    `ULPIN: ${code}`,
    `Building: ${u.building_name ?? "—"}`,
    `Owner: ${u.owner_name ?? "Not recorded"}`,
    `Place: ${place || "—"}`,
    c ? `Position: X=${num(c.x)} Y=${num(c.y)} Z=${num(c.z)} m` : `Position: not placed`,
    `Floor ${u.floor_number ?? "—"} · Unit ${u.unit_number}`,
  ].join("\n");
}

/* ------------------------------------------------------------------- result -- */

function MintedUnit({ unit, place }: { unit: ThreeDUnitResponse; place: string }) {
  const ref = React.useRef<HTMLDivElement>(null);
  const code = unit.short_code ?? unit.ulpin ?? unit.unit_number;
  const download = React.useCallback(() => {
    const svg = ref.current?.querySelector("svg");
    if (!svg) return;
    const blob = new Blob([new XMLSerializer().serializeToString(svg)], {
      type: "image/svg+xml",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${code}.svg`;
    a.click();
    URL.revokeObjectURL(url);
  }, [code]);

  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border p-3">
      <div ref={ref} className="rounded-md border bg-white p-2">
        <QRCode value={qrPayload(unit, place)} size={116} level="M" aria-label={`QR for ${code}`} />
      </div>
      <UlpinChip code={code} />
      <p className="text-xs text-muted-foreground">
        Floor {unit.floor_number ?? "—"} · Unit {unit.unit_number}
        {unit.coordinates ? (
          <span className="ml-1 tabular-nums">· Z={num(unit.coordinates.z)}m</span>
        ) : null}
      </p>
      <Button variant="ghost" size="sm" onClick={download}>
        <Download /> QR
      </Button>
    </div>
  );
}

/* -------------------------------------------------------------- the section -- */

type Mode = "new" | "existing";

export function UlpinGenerateCard() {
  const qc = useQueryClient();
  const [mode, setMode] = React.useState<Mode>("new");
  const [result, setResult] = React.useState<ThreeDGenerateResponse | null>(null);
  const [place, setPlace] = React.useState("");

  // Existing-building mode.
  const list = useQuery({
    queryKey: ["buildings", "generate-picker"],
    queryFn: () => buildings.list({ page: 1, page_size: 100 }),
  });
  const [existingId, setExistingId] = React.useState<string | null>(null);
  const [existingFloors, setExistingFloors] = React.useState("3");
  const [existingPerFloor, setExistingPerFloor] = React.useState("2");

  const form = useForm<GenerateValues>({
    resolver: zodResolver(generateSchema),
    defaultValues: {
      building_name: "",
      address_line1: "",
      city: "",
      state_code: "KA",
      latitude: "" as unknown as number,
      longitude: "" as unknown as number,
      floors: 3 as unknown as number,
      units_per_floor: 2 as unknown as number,
      property_type: "RESIDENTIAL",
    },
  });
  const errors = form.formState.errors;

  const afterMint = (res: ThreeDGenerateResponse, placeLabel: string) => {
    setResult(res);
    setPlace(placeLabel);
    void qc.invalidateQueries({ queryKey: ["buildings"] });
    void qc.invalidateQueries({ queryKey: ["3d-building"] });
    void qc.invalidateQueries({ queryKey: ["dashboard"] });
    toast.success(
      `${res.ulpins_minted} ULPIN${res.ulpins_minted === 1 ? "" : "s"} minted for ${res.building_name}.`,
    );
  };

  // New property: create the building, then scaffold + mint in one go.
  const createAndMint = useMutation({
    mutationFn: async (v: GenerateValues) => {
      const reg = deriveRegistry(v.state_code, v.city);
      const stateName =
        STATE_CODES.find((s) => s.value === v.state_code)?.label ?? v.state_code;
      const building = await buildings.create({
        building_name: v.building_name,
        address_line1: v.address_line1,
        city: v.city,
        state: stateName,
        latitude: v.latitude,
        longitude: v.longitude,
        total_floors: v.floors,
        parcel_ulpin: reg.parcel_ulpin,
        jurisdiction_code: reg.jurisdiction_code,
      });
      const res = await threeDUlpin.generate({
        building_id: building.building_id,
        floors: v.floors,
        units_per_floor: v.units_per_floor,
        property_type: v.property_type,
      });
      return { res, place: [v.city, stateName].filter(Boolean).join(", ") };
    },
    onSuccess: ({ res, place }) => afterMint(res, place),
    onError: (e) => {
      if (e instanceof ApiError && e.fieldErrors) {
        for (const [f, m] of Object.entries(e.fieldErrors)) {
          form.setError(f as keyof GenerateValues, { message: m });
        }
      }
      toast.error(errorMessage(e));
    },
  });

  // Existing building: scaffold + mint against a chosen id.
  const mintExisting = useMutation({
    mutationFn: async () => {
      if (!existingId) throw new Error("Choose a building first.");
      const res = await threeDUlpin.generate({
        building_id: existingId,
        floors: Number(existingFloors) || undefined,
        units_per_floor: Number(existingPerFloor) || undefined,
      });
      const b = list.data?.items.find((x) => x.building_id === existingId);
      const place = b ? [b.city, b.state_code].filter(Boolean).join(", ") : "";
      return { res, place };
    },
    onSuccess: ({ res, place }) => afterMint(res, place),
    onError: (e) => toast.error(errorMessage(e)),
  });

  const busy = createAndMint.isPending || mintExisting.isPending;

  return (
    <Card>
      <CardHeader className="space-y-3">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="size-4 text-primary" aria-hidden />
            Generate a ULPIN &amp; QR code
          </CardTitle>
          <CardDescription>
            Enter a property&apos;s details and mint its 3D ULPINs. Each unit gets a unique
            identifier and a scannable QR code carrying its record.
          </CardDescription>
        </div>
        <div className="flex w-fit items-center gap-1 rounded-md border p-0.5">
          <Button
            size="sm"
            variant={mode === "new" ? "secondary" : "ghost"}
            onClick={() => setMode("new")}
          >
            New property
          </Button>
          <Button
            size="sm"
            variant={mode === "existing" ? "secondary" : "ghost"}
            onClick={() => setMode("existing")}
          >
            Existing building
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        {mode === "new" ? (
          <form
            onSubmit={form.handleSubmit((v) => createAndMint.mutate(v))}
            className="grid gap-4 sm:grid-cols-2"
            noValidate
          >
            <Field
              label="Building name"
              htmlFor="building_name"
              required
              className="sm:col-span-2"
              error={errors.building_name?.message}
            >
              <Input
                {...fieldProps("building_name", errors.building_name?.message)}
                {...form.register("building_name")}
                placeholder="Maitri Heights"
              />
            </Field>

            <Field
              label="Street address"
              htmlFor="address_line1"
              required
              className="sm:col-span-2"
              error={errors.address_line1?.message}
            >
              <Input
                {...fieldProps("address_line1", errors.address_line1?.message)}
                {...form.register("address_line1")}
                placeholder="27 Residency Road"
              />
            </Field>

            <Field label="City" htmlFor="city" required error={errors.city?.message}>
              <Input
                {...fieldProps("city", errors.city?.message)}
                {...form.register("city")}
                placeholder="Bengaluru"
              />
            </Field>

            <Field label="State" htmlFor="state_code" required error={errors.state_code?.message}>
              <Select
                value={form.watch("state_code")}
                onValueChange={(v) => form.setValue("state_code", v, { shouldValidate: true })}
              >
                <SelectTrigger id="state_code">
                  <SelectValue placeholder="State" />
                </SelectTrigger>
                <SelectContent>
                  {STATE_CODES.map((s) => (
                    <SelectItem key={s.value} value={s.value}>
                      {s.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label="Latitude" htmlFor="latitude" required error={errors.latitude?.message}>
              <Input
                {...fieldProps("latitude", errors.latitude?.message)}
                {...form.register("latitude")}
                inputMode="decimal"
                placeholder="12.9717"
              />
            </Field>

            <Field label="Longitude" htmlFor="longitude" required error={errors.longitude?.message}>
              <Input
                {...fieldProps("longitude", errors.longitude?.message)}
                {...form.register("longitude")}
                inputMode="decimal"
                placeholder="77.5946"
              />
            </Field>

            <Field label="Floors" htmlFor="floors" required error={errors.floors?.message}>
              <Input
                {...fieldProps("floors", errors.floors?.message)}
                {...form.register("floors")}
                inputMode="numeric"
                placeholder="3"
              />
            </Field>

            <Field
              label="Units per floor"
              htmlFor="units_per_floor"
              required
              error={errors.units_per_floor?.message}
            >
              <Input
                {...fieldProps("units_per_floor", errors.units_per_floor?.message)}
                {...form.register("units_per_floor")}
                inputMode="numeric"
                placeholder="2"
              />
            </Field>

            <Field
              label="Property type"
              htmlFor="property_type"
              className="sm:col-span-2"
              error={errors.property_type?.message}
            >
              <Select
                value={form.watch("property_type")}
                onValueChange={(v) =>
                  form.setValue("property_type", v as GenerateValues["property_type"])
                }
              >
                <SelectTrigger id="property_type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PROPERTY_TYPES.map((p) => (
                    <SelectItem key={p.value} value={p.value}>
                      {p.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <div className="sm:col-span-2">
              <Button type="submit" loading={busy} disabled={busy} className="w-full sm:w-auto">
                <Sparkles /> {busy ? "Generating…" : "Generate ULPIN & QR"}
              </Button>
            </div>
          </form>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Building" htmlFor="existing_building" className="sm:col-span-2">
              <Select
                value={existingId ?? ""}
                onValueChange={(v) => setExistingId(v)}
                disabled={list.isLoading || (list.data?.items.length ?? 0) === 0}
              >
                <SelectTrigger id="existing_building">
                  <SelectValue placeholder="Choose a registered building" />
                </SelectTrigger>
                <SelectContent>
                  {list.data?.items.map((b) => (
                    <SelectItem key={b.building_id} value={b.building_id}>
                      {b.building_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label="Floors" htmlFor="existing_floors">
              <Input
                id="existing_floors"
                value={existingFloors}
                onChange={(e) => setExistingFloors(e.target.value)}
                inputMode="numeric"
                placeholder="3"
              />
            </Field>

            <Field label="Units per floor" htmlFor="existing_per_floor">
              <Input
                id="existing_per_floor"
                value={existingPerFloor}
                onChange={(e) => setExistingPerFloor(e.target.value)}
                inputMode="numeric"
                placeholder="2"
              />
            </Field>

            <div className="sm:col-span-2">
              <Button
                onClick={() => mintExisting.mutate()}
                loading={busy}
                disabled={busy || !existingId}
                className="w-full sm:w-auto"
              >
                <Sparkles /> {busy ? "Generating…" : "Generate ULPIN & QR"}
              </Button>
            </div>
          </div>
        )}

        {/* Result */}
        {result ? (
          <div className="space-y-4 border-t pt-4">
            <div className="flex flex-wrap items-center gap-4 text-sm">
              <span className="flex items-center gap-1.5 font-medium">
                <Building2 className="size-4 text-muted-foreground" aria-hidden />
                {result.building_name}
              </span>
              <span className="flex items-center gap-1.5 text-muted-foreground">
                <Layers className="size-4" aria-hidden />
                {result.floors_created} floor{result.floors_created === 1 ? "" : "s"} ·{" "}
                {result.units_created} unit{result.units_created === 1 ? "" : "s"}
              </span>
              <span className="flex items-center gap-1.5 text-verified">
                <QrCode className="size-4" aria-hidden />
                {result.ulpins_minted} ULPIN{result.ulpins_minted === 1 ? "" : "s"} minted
              </span>
              {place ? (
                <span className="flex items-center gap-1.5 text-muted-foreground">
                  <MapPin className="size-4" aria-hidden />
                  {place}
                </span>
              ) : null}
            </div>

            {result.units.length > 0 ? (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {result.units.map((u) => (
                  <MintedUnit key={u.unit_id} unit={u} place={place} />
                ))}
              </div>
            ) : (
              <EmptyState
                icon={QrCode}
                title="No units minted"
                description="The building was created but no units were scaffolded. Try again with at least one floor and one unit per floor."
              />
            )}

            {result.skipped.length > 0 ? (
              <Alert>
                <AlertDescription>
                  Skipped {result.skipped.length}: {result.skipped.join(", ")}
                </AlertDescription>
              </Alert>
            ) : null}
          </div>
        ) : (
          <p className={cn("text-xs text-muted-foreground", result ? "hidden" : "")}>
            The 14-character parcel ULPIN and jurisdiction code are derived for you — you only
            enter the details above.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
