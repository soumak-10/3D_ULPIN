/**
 * Mirrors of the FastAPI response shapes.
 *
 * Hand-written rather than generated, because the generated client for a 56-path
 * OpenAPI document is larger than the application and the pages only consume a
 * dozen of those shapes. The names match the Pydantic models one-to-one so a
 * change on either side is findable by grep.
 */

/* ------------------------------------------------------------------ enums --- */

export type Role = "ADMIN" | "PROPERTY_OFFICER" | "OWNER" | "TENANT";

export type PropertyType =
  | "RESIDENTIAL"
  | "COMMERCIAL"
  | "INDUSTRIAL"
  | "INSTITUTIONAL"
  | "MIXED_USE"
  | "PARKING"
  | "UTILITY"
  | "COMMON_AREA";

export type OccupancyStatus = "OCCUPIED" | "VACANT" | "UNDER_RENOVATION" | "SEALED";

export type VerificationOutcome =
  | "VERIFIED"
  | "PENDING_VERIFICATION"
  | "INVALID_CLAIM"
  | "UNAUTHORIZED_OCCUPANCY";

export type AlertSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

export type AlertStatus =
  | "OPEN"
  | "INVESTIGATING"
  | "CONFIRMED"
  | "DISMISSED"
  | "RESOLVED";

export type FraudRuleCode =
  | "MULTIPLE_OWNERS"
  | "DUPLICATE_ULPIN"
  | "OWNERSHIP_MISMATCH"
  | "TENANT_MISMATCH"
  | "UNAUTHORIZED_OCCUPANCY";

export type StoreyClass = "BASEMENT" | "STILT" | "PODIUM" | "GROUND" | "MEZZANINE" | "UPPER" | "TERRACE" | "AIR_RIGHTS";

/* -------------------------------------------------------------------- auth -- */

export interface UserResponse {
  user_id: string;
  email: string;
  full_name: string;
  role: Role;
  phone: string | null;
  is_active: boolean;
  is_verified: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: UserResponse;
}

/* ---------------------------------------------------------------- property -- */

export interface BuildingResponse {
  building_id: string;
  building_name: string;
  short_code: string | null;
  building_sequence: number | null;
  address_line1: string;
  address_line2: string | null;
  locality: string | null;
  city: string;
  district: string | null;
  state_code: string;
  pincode: string | null;
  latitude: number;
  longitude: number;
  total_floors: number;
  basement_floors: number;
  building_height_m: number | null;
  year_built: number | null;
  status: string;
  unit_count?: number;
  created_at: string;
  updated_at: string | null;
}

export interface FloorResponse {
  floor_id: string;
  building_id: string;
  floor_number: number;
  floor_label: string | null;
  storey_class: StoreyClass;
  floor_height_m: number | null;
  elevation_m: number | null;
  carpet_area_sqm: number | null;
  unit_count?: number;
}

export interface UnitResponse {
  unit_id: string;
  building_id: string;
  floor_id: string;
  unit_number: string;
  unit_code: string | null;
  floor_number: number;
  property_type: PropertyType;
  occupancy_status: OccupancyStatus;
  unit_status: string;
  carpet_area_sqm: number | null;
  built_up_area_sqm: number | null;
  verification_outcome: VerificationOutcome | null;
  last_verified_at: string | null;
  volume_cum: number | null;
  is_solid_valid: boolean | null;
  ulpin_code: string | null;
  short_code: string | null;
  created_at: string;
  updated_at: string | null;

  // 3D placement in the building's own frame, metres, origin at the building
  // footprint centroid. Null on a unit that has not been placed yet, which the
  // scene renders with its fallback layout rather than at the origin.
  x_coordinate: number | null;
  y_coordinate: number | null;
  z_coordinate: number | null;
  width_m: number | null;
  length_m: number | null;
  height_m: number | null;
}

// ---------------------------------------------------------------------------
// 3D ULPIN
// ---------------------------------------------------------------------------

export interface Coordinates3D {
  x: number;
  y: number;
  z: number;
}

export interface Dimensions3D {
  width: number;
  length: number;
  height: number;
  volume_cum: number | null;
}

export interface ThreeDUnitResponse {
  unit_id: string;
  unit_number: string;
  floor_number: number | null;
  building_id: string;
  building_name: string | null;
  building_code: string | null;
  ulpin: string | null;
  short_code: string | null;
  property_type: PropertyType;
  unit_status: string;
  occupancy_status: OccupancyStatus;
  verification_status: VerificationOutcome | null;
  coordinates: Coordinates3D | null;
  dimensions: Dimensions3D | null;
  carpet_area_sqm: number | null;
  // GeoJSON MultiPolygon/Polygon with Z, EPSG:4326. Only returned by the
  // single-unit routes; the building list omits it to keep the payload small.
  geometry_3d: Record<string, unknown> | null;
  owner_name: string | null;
  tenant_name: string | null;
}

export interface ThreeDBuildingResponse {
  building_id: string;
  building_name: string;
  building_code: string | null;
  state: string | null;
  city: string | null;
  latitude: number;
  longitude: number;
  total_floors: number;
  unit_count: number;
  located_count: number;
  floor_numbers: number[];
  units: ThreeDUnitResponse[];
}

export interface ThreeDGenerateResponse {
  building_id: string;
  building_name: string;
  floors_created: number;
  units_created: number;
  units_placed: number;
  ulpins_minted: number;
  skipped: string[];
  units: ThreeDUnitResponse[];
}

export interface ThreeDGenerateRequest {
  building_id?: string;
  unit_id?: string;
  floors?: number;
  units_per_floor?: number;
  property_type?: PropertyType;
  width?: number;
  length?: number;
  height?: number;
  issue?: boolean;
  overwrite?: boolean;
}

export interface OwnerResponse {
  owner_id: string;
  owner_type: string;
  full_name: string | null;
  organisation_name: string | null;
  display_name: string;
  email: string | null;
  phone: string | null;
  address_line1: string | null;
  city: string | null;
  state_code: string | null;
  pincode: string | null;
  kyc_status: string;
  created_at: string;
}

export interface TenantResponse {
  tenant_id: string;
  unit_id: string;
  full_name: string;
  email: string | null;
  phone: string | null;
  status: string;
  lease_start: string | null;
  lease_end: string | null;
  monthly_rent: number | null;
  deposit_amount: number | null;
  agreement_number: string | null;
  created_at: string;
}

/* ------------------------------------------------------------------- ulpin -- */

export interface UlpinResponse {
  ulpin_id: string;
  unit_id: string;
  ulpin_code: string;
  short_code: string;
  display_code: string;
  state_code: string;
  city_code: string;
  building_sequence: number;
  storey_class: StoreyClass;
  floor_number: number;
  unit_sequence: number;
  status: string;
  version: number;
  issued_at: string;
  superseded_at: string | null;
  superseded_by: string | null;
  checksum: string | null;
}

export interface UlpinGenerateRequest {
  unit_id: string;
  dry_run?: boolean;
  reason?: string | null;
}

export interface UlpinPreviewResponse {
  short_code: string;
  ulpin_code: string;
  would_collide: boolean;
  notes: string[];
}

/* ------------------------------------------------------------ verification -- */

export interface CheckResponse {
  check: string;
  passed: boolean;
  blocking: boolean;
  detail: string;
  evidence: Record<string, unknown>;
}

export interface VerificationResponse {
  verification_id: string | null;
  ulpin_code: string;
  short_code: string | null;
  unit_id: string | null;
  outcome: VerificationOutcome;
  headline: string;
  confidence: number;
  checks: CheckResponse[];
  checks_passed: number;
  checks_failed: number;
  verified_at: string | null;
  verified_by: string | null;
  owner_names: string[];
  tenant_name: string | null;
  building_name: string | null;
  floor_number: number | null;
  unit_number: string | null;
  occupancy_status: OccupancyStatus | null;
}

export interface VerificationRecordResponse {
  verification_id: string;
  ulpin_id: string | null;
  unit_id: string | null;
  ulpin_code: string | null;
  outcome: VerificationOutcome | null;
  status: string;
  method: string | null;
  confidence: number | null;
  checks_passed: number | null;
  checks_failed: number | null;
  remarks: string | null;
  rejection_reason: string | null;
  reference_no: string | null;
  requested_by: string | null;
  verified_by: string | null;
  verified_at: string | null;
  created_at: string;
}

/* ------------------------------------------------------------------- fraud -- */

export interface AlertResponse {
  alert_id: string;
  rule_code: FraudRuleCode;
  severity: AlertSeverity;
  status: AlertStatus;
  title: string;
  description: string | null;
  confidence: number | null;
  risk_score: number | null;
  measured_value: number | null;
  threshold_value: number | null;
  evidence: Record<string, unknown> | null;
  fingerprint: string | null;
  unit_id: string | null;
  building_id: string | null;
  ulpin_id: string | null;
  owner_id: string | null;
  tenant_id: string | null;
  short_code: string | null;
  unit_number: string | null;
  building_name: string | null;
  floor_number: number | null;
  is_false_positive: boolean;
  assigned_to: string | null;
  assigned_at: string | null;
  resolved_by: string | null;
  resolved_at: string | null;
  resolution_notes: string | null;
  detected_at: string;
  created_at: string;
}

export interface RuleSummary {
  rule_code: FraudRuleCode;
  title: string;
  description: string;
  severity: AlertSeverity;
  open_count: number;
  total_count: number;
}

export interface FraudSummaryResponse {
  open_total: number;
  by_severity: Record<string, number>;
  by_status: Record<string, number>;
  rules: RuleSummary[];
}

export interface RuleFinding {
  rule_code: FraudRuleCode;
  severity: AlertSeverity;
  title: string;
  description: string;
  subject: string;
  fingerprint: string;
  confidence: number;
  risk_score: number;
  measured_value: number | null;
  threshold_value: number | null;
  unit_id: string | null;
  building_id: string | null;
  ulpin_id: string | null;
  evidence: Record<string, unknown>;
}

export interface ScanResponse {
  scanned_units: number;
  rules_run: FraudRuleCode[];
  findings: RuleFinding[];
  alerts_created: number;
  alerts_updated: number;
  by_severity: Record<string, number>;
  took_ms: number;
  dry_run: boolean;
}

/* ------------------------------------------------------------------ search -- */

export type SearchMode = "auto" | "ulpin" | "owner" | "tenant" | "building";

export interface SearchResult {
  unit_id: string;
  unit_number: string;
  unit_type: PropertyType | null;
  unit_status: string | null;
  occupancy_status: OccupancyStatus | null;
  carpet_area_sqm: number | null;

  floor_id: string | null;
  floor_number: number | null;
  floor_label: string | null;

  building_id: string | null;
  building_name: string | null;
  address: string | null;
  city: string | null;
  state: string | null;
  latitude: number | null;
  longitude: number | null;

  ulpin_id: string | null;
  short_code: string | null;
  ulpin_code: string | null;

  verification_outcome: VerificationOutcome | null;
  last_verified_at: string | null;
  open_alert_count: number;

  owner_names: string[];
  tenant_name: string | null;

  /** Why this row matched, e.g. "owner: Rina Banerjee". */
  matched_on: string | null;
  rank: number | null;
}

/**
 * Note `pages` is absent: it is a Python `@property` on the Pydantic model and
 * so is never serialised. Derive it from total/page_size on the client.
 */
export interface SearchResponse {
  query: string;
  mode_used: SearchMode;
  items: SearchResult[];
  total: number;
  page: number;
  page_size: number;
  took_ms: number | null;
}

export interface SuggestItem {
  label: string;
  value: string;
  kind: "ulpin" | "owner" | "tenant" | "building";
  hint: string | null;
}

export interface SuggestResponse {
  items: SuggestItem[];
}

/* --------------------------------------------------------------- dashboard -- */

export interface StatCard {
  key: string;
  label: string;
  value: number;
  delta: number | null;
  delta_label: string | null;
  tone: string;
  hint: string | null;
}

export interface SeriesPoint {
  name: string;
  value: number;
  tone?: string | null;
}

export interface TrendPoint {
  date: string;
  total: number;
  low: number;
  medium: number;
  high: number;
  critical: number;
}

export interface OccupancyPoint {
  date: string;
  occupied: number;
  vacant: number;
}

export interface ActivityRow {
  log_id: string;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  ulpin_code: string | null;
  actor_role: string | null;
  success: boolean;
  created_at: string;
}

export interface VerificationRow {
  verification_id: string;
  ulpin_code: string | null;
  outcome: VerificationOutcome | null;
  confidence: number | null;
  verified_at: string | null;
  created_at: string;
}

export interface AlertRow {
  alert_id: string;
  rule_code: FraudRuleCode;
  severity: AlertSeverity;
  status: AlertStatus;
  title: string;
  short_code: string | null;
  detected_at: string;
}

export interface DashboardResponse {
  generated_at: string;
  cards: StatCard[];
  property_distribution: SeriesPoint[];
  verification_status: SeriesPoint[];
  fraud_trend: TrendPoint[];
  occupancy_trend: OccupancyPoint[];
  recent_activities: ActivityRow[];
  recent_verifications: VerificationRow[];
  recent_alerts: AlertRow[];
}

/* --------------------------------------------------------------- envelopes -- */

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

/** RFC 9457 problem+json, which is what the API returns for every error. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  instance?: string;
  code?: string;
  // A field -> messages map, matching the RequestValidationError handler in
  // app/core/error_handlers.py. One field can fail several rules at once, which
  // is why each value is a list: a rejected password commonly returns four.
  errors?: Record<string, string[]>;
}
