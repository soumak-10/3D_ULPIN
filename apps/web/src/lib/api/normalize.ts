/**
 * Reconcile the live API's wire shape with the TypeScript contract.
 *
 * The FastAPI responses drifted from the hand-written types in `@/types/api` in
 * two ways that crash the pages which trust those types:
 *
 *   1. PostgreSQL NUMERIC columns serialise as JSON *strings* ("12.9717000",
 *      "6.455"), not numbers. `latitude.toFixed(5)` on a string throws, taking
 *      the buildings list and detail pages down; a string fed to
 *      `THREE.BoxGeometry` and `x + w / 2` concatenates into NaN, so the 3D
 *      viewer draws nothing.
 *
 *   2. A few fields are named differently on the wire than in the contract the
 *      UI reads: `state` vs `state_code`, `postal_code` vs `pincode`,
 *      `total_units` vs `unit_count`, `floor_type` vs `storey_class`,
 *      `elevation_base_m` vs `elevation_m`, `unit_type` vs `property_type`.
 *
 * Normalising here — at the one boundary every read flows through — lets the
 * rest of the app keep trusting the declared types: numbers are numbers and the
 * field names match. Raw fields are preserved alongside the mapped ones, so a
 * later contract change is additive rather than breaking.
 */

import type { AlertResponse, BuildingResponse, FloorResponse, Page, UnitResponse } from "@/types/api";

type Raw = Record<string, unknown>;

/** Coerce a wire value that may be a NUMERIC-as-string, a number, or null. */
function num(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

function asRaw(value: unknown): Raw {
  return (value ?? {}) as Raw;
}

export function normalizeBuilding(raw: unknown): BuildingResponse {
  const b = asRaw(raw);
  return {
    ...b,
    // latitude/longitude are non-null in the contract and always sent; the ?? 0
    // guards a malformed row so the page renders "0.00000" rather than crashing.
    latitude: num(b.latitude) ?? 0,
    longitude: num(b.longitude) ?? 0,
    building_height_m: num(b.building_height_m),
    state_code: (b.state_code ?? b.state ?? "") as string,
    pincode: (b.pincode ?? b.postal_code ?? null) as string | null,
    unit_count: (b.unit_count ?? b.total_units) as number | undefined,
    short_code: (b.short_code ?? null) as string | null,
  } as unknown as BuildingResponse;
}

export function normalizeFloor(raw: unknown): FloorResponse {
  const f = asRaw(raw);
  return {
    ...f,
    storey_class: (f.storey_class ?? f.floor_type ?? "GROUND") as FloorResponse["storey_class"],
    floor_height_m: num(f.floor_height_m),
    elevation_m: num(f.elevation_m ?? f.elevation_base_m),
    carpet_area_sqm: num(f.carpet_area_sqm ?? f.gross_area_sqm),
    unit_count: (num(f.unit_count) ?? 0) as number,
  } as unknown as FloorResponse;
}

export function normalizeUnit(raw: unknown): UnitResponse {
  const u = asRaw(raw);
  return {
    ...u,
    property_type: (u.property_type ?? u.unit_type ?? "RESIDENTIAL") as UnitResponse["property_type"],
    x_coordinate: num(u.x_coordinate),
    y_coordinate: num(u.y_coordinate),
    z_coordinate: num(u.z_coordinate),
    width_m: num(u.width_m),
    length_m: num(u.length_m),
    height_m: num(u.height_m),
    volume_cum: num(u.volume_cum),
    carpet_area_sqm: num(u.carpet_area_sqm),
    built_up_area_sqm: num(u.built_up_area_sqm),
    ulpin_code: (u.ulpin_code ?? null) as string | null,
    short_code: (u.short_code ?? null) as string | null,
  } as unknown as UnitResponse;
}

export function normalizeAlert(raw: unknown): AlertResponse {
  const a = asRaw(raw);
  return {
    ...a,
    // risk_score/confidence/measured_value/threshold_value are NUMERIC on the
    // wire → strings. risk_score.toFixed(0) crashes the fraud list and detail
    // pages outright; the others only work by accidental string coercion.
    risk_score: num(a.risk_score),
    confidence: num(a.confidence),
    measured_value: num(a.measured_value),
    threshold_value: num(a.threshold_value),
  } as unknown as AlertResponse;
}

export function normalizeBuildingPage(page: Page<BuildingResponse>): Page<BuildingResponse> {
  return { ...page, items: (page?.items ?? []).map(normalizeBuilding) };
}

export function normalizeUnitPage(page: Page<UnitResponse>): Page<UnitResponse> {
  return { ...page, items: (page?.items ?? []).map(normalizeUnit) };
}

export function normalizeAlertPage(page: Page<AlertResponse>): Page<AlertResponse> {
  return { ...page, items: (page?.items ?? []).map(normalizeAlert) };
}
