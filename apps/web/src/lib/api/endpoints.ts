/**
 * Endpoint bindings, one function per API route the UI actually uses.
 *
 * Pages never call `api.get("/some/path")` directly. Keeping the paths here
 * means a route rename is one file to change, and it gives every call site a
 * return type without repeating the generic.
 *
 * The shapes below follow the router as registered, not as one might guess:
 * units and tenancies are created *under their parent* (`/buildings/{id}/units`,
 * `/units/{id}/tenancies`) because a unit without a building and a tenancy
 * without a unit are not things the register can hold.
 */

import { api } from "./client";
import type {
  AlertResponse,
  BuildingResponse,
  DashboardResponse,
  FloorResponse,
  FraudSummaryResponse,
  OwnerResponse,
  Page,
  ScanResponse,
  SearchMode,
  SearchResponse,
  SuggestResponse,
  TenantResponse,
  ThreeDBuildingResponse,
  ThreeDGenerateRequest,
  ThreeDGenerateResponse,
  ThreeDUnitResponse,
  TokenResponse,
  TrendPoint,
  UlpinPreviewResponse,
  UlpinResponse,
  UnitResponse,
  UserResponse,
  VerificationRecordResponse,
  VerificationResponse,
} from "@/types/api";

export interface MessageResponse {
  message: string;
  code?: string | null;
}

/** `/auth/verify-reset-otp` — authorisation to set a new password. */
export interface ResetTicketResponse {
  reset_token: string;
  expires_in: number;
}

/* -------------------------------------------------------------------- auth -- */

export const auth = {
  login: (email: string, password: string) =>
    api.post<TokenResponse>("/auth/login", { email, password }, { anonymous: true }),

  register: (body: {
    email: string;
    password: string;
    confirm_password: string;
    full_name: string;
    phone?: string | null;
    role?: string;
  }) => api.post<UserResponse>("/auth/register", body, { anonymous: true }),

  logout: () => api.post<MessageResponse>("/auth/logout"),

  me: () => api.get<UserResponse>("/auth/me"),

  forgotPassword: (email: string) =>
    api.post<MessageResponse>("/auth/forgot-password", { email }, { anonymous: true }),

  // -- One-time passcodes ---------------------------------------------------
  verifyOtp: (email: string, code: string) =>
    api.post<MessageResponse>("/auth/verify-otp", { email, code }, { anonymous: true }),

  resendOtp: (email: string, purpose: "EMAIL_VERIFY" | "PASSWORD_RESET" = "EMAIL_VERIFY") =>
    api.post<MessageResponse>("/auth/resend-otp", { email, purpose }, { anonymous: true }),

  // Returns the short-lived ticket that resetPassword consumes. The code itself
  // is spent server-side by this call.
  verifyResetOtp: (email: string, code: string) =>
    api.post<ResetTicketResponse>(
      "/auth/verify-reset-otp",
      { email, code },
      { anonymous: true },
    ),

  // The API applies its password policy to `password` and requires the
  // confirmation alongside it; sending `new_password` alone is rejected as a
  // missing field.
  resetPassword: (token: string, password: string) =>
    api.post<MessageResponse>(
      "/auth/reset-password",
      { token, password, confirm_password: password },
      { anonymous: true },
    ),

  changePassword: (current_password: string, password: string) =>
    api.post<MessageResponse>("/auth/change-password", {
      current_password,
      password,
      confirm_password: password,
    }),

  sessions: () => api.get<unknown[]>("/auth/sessions"),
};

/* ---------------------------------------------------------------- property -- */

export const buildings = {
  list: (
    params: {
      page?: number;
      page_size?: number;
      q?: string;
      city?: string;
      state_code?: string;
      status?: string;
    } = {},
  ) => api.get<Page<BuildingResponse>>("/buildings", { query: params }),

  get: (id: string) => api.get<BuildingResponse>(`/buildings/${id}`),

  create: (body: Record<string, unknown>) => api.post<BuildingResponse>("/buildings", body),

  update: (id: string, body: Record<string, unknown>) =>
    api.patch<BuildingResponse>(`/buildings/${id}`, body),

  setStatus: (id: string, status: string, reason?: string) =>
    api.post<BuildingResponse>(`/buildings/${id}/status`, { status, reason: reason ?? null }),

  remove: (id: string) => api.delete<MessageResponse>(`/buildings/${id}`),

  floors: (id: string) => api.get<FloorResponse[]>(`/buildings/${id}/floors`),

  createFloor: (id: string, body: Record<string, unknown>) =>
    api.post<FloorResponse>(`/buildings/${id}/floors`, body),

  updateFloor: (floorId: string, body: Record<string, unknown>) =>
    api.patch<FloorResponse>(`/buildings/floors/${floorId}`, body),

  units: (id: string, params: { page?: number; page_size?: number } = {}) =>
    api.get<Page<UnitResponse>>(`/buildings/${id}/units`, { query: params }),

  createUnit: (id: string, body: Record<string, unknown>) =>
    api.post<UnitResponse>(`/buildings/${id}/units`, body),

  createUnitsBulk: (id: string, body: Record<string, unknown>) =>
    api.post<UnitResponse[]>(`/buildings/${id}/units/bulk`, body),
};

export const units = {
  list: (
    params: {
      page?: number;
      page_size?: number;
      q?: string;
      building_id?: string;
      floor_id?: string;
      property_type?: string;
      occupancy_status?: string;
      verification_outcome?: string;
    } = {},
  ) => api.get<Page<UnitResponse>>("/units", { query: params }),

  get: (id: string) => api.get<UnitResponse>(`/units/${id}`),

  update: (id: string, body: Record<string, unknown>) =>
    api.patch<UnitResponse>(`/units/${id}`, body),

  setOccupancy: (id: string, occupancy_status: string, reason?: string) =>
    api.post<UnitResponse>(`/units/${id}/occupancy`, {
      occupancy_status,
      reason: reason ?? null,
    }),

  remove: (id: string) => api.delete<MessageResponse>(`/units/${id}`),

  ownership: (id: string) => api.get<unknown[]>(`/units/${id}/ownership`),

  addOwnership: (id: string, body: Record<string, unknown>) =>
    api.post<unknown>(`/units/${id}/ownership`, body),

  tenancies: (id: string) => api.get<TenantResponse[]>(`/units/${id}/tenancies`),

  addTenancy: (id: string, body: Record<string, unknown>) =>
    api.post<TenantResponse>(`/units/${id}/tenancies`, body),
};

// Vertical property mapping. Kept apart from `units` because these answer a
// different question: units.list says what is registered, this says where in
// space it is and how much room it takes up.
export const threeDUlpin = {
  /** Place units in space and mint their identifiers. Pass `unit_id` to place
   *  one, or `building_id` plus `floors`/`units_per_floor` to build the storeys
   *  and units as well. */
  generate: (body: ThreeDGenerateRequest) =>
    api.post<ThreeDGenerateResponse>("/3d-ulpin/generate", body),

  /** Every unit of a building with coordinates and dimensions attached. */
  byBuilding: (buildingId: string) =>
    api.get<ThreeDBuildingResponse>(`/3d-ulpin/building/${buildingId}`),

  /** One unit, including its solid as GeoJSON. */
  byUnit: (unitId: string) => api.get<ThreeDUnitResponse>(`/3d-ulpin/${unitId}`),

  /** Resolve the code printed on a deed, e.g. WB-KOL-B001-F03-U301. */
  byUlpin: (ulpin: string) =>
    api.get<ThreeDUnitResponse>(`/3d-ulpin/view/${encodeURIComponent(ulpin)}`),
};

export const owners = {
  list: (params: { page?: number; page_size?: number; q?: string; owner_type?: string } = {}) =>
    api.get<Page<OwnerResponse>>("/owners", { query: params }),

  get: (id: string) => api.get<OwnerResponse>(`/owners/${id}`),

  me: () => api.get<OwnerResponse>("/owners/me"),

  create: (body: Record<string, unknown>) => api.post<OwnerResponse>("/owners", body),

  update: (id: string, body: Record<string, unknown>) =>
    api.patch<OwnerResponse>(`/owners/${id}`, body),
};

export const tenants = {
  me: () => api.get<TenantResponse[]>("/tenants/me"),

  get: (id: string) => api.get<TenantResponse>(`/tenants/${id}`),

  update: (id: string, body: Record<string, unknown>) =>
    api.patch<TenantResponse>(`/tenants/${id}`, body),

  end: (id: string, body: { ended_on?: string; reason?: string } = {}) =>
    api.post<TenantResponse>(`/tenants/${id}/end`, body),
};

/* ------------------------------------------------------------------- ulpin -- */

export const ulpins = {
  generate: (unit_id: string, reason?: string) =>
    api.post<UlpinResponse>("/ulpins/generate", { unit_id, reason: reason ?? null }),

  preview: (unit_id: string) =>
    api.post<UlpinPreviewResponse>("/ulpins/generate", { unit_id, dry_run: true }),

  validate: (code: string) =>
    api.post<{ valid: boolean; errors: string[]; parsed: Record<string, unknown> | null }>(
      "/ulpins/validate",
      { code },
    ),

  resolve: (code: string) =>
    api.get<UlpinResponse>(`/ulpins/resolve/${encodeURIComponent(code)}`),

  get: (id: string) => api.get<UlpinResponse>(`/ulpins/${id}`),

  list: (
    params: {
      page?: number;
      page_size?: number;
      building_id?: string;
      status?: string;
      q?: string;
    } = {},
  ) => api.get<Page<UlpinResponse>>("/ulpins", { query: params }),

  sequences: (params: { state_code?: string; city_code?: string } = {}) =>
    api.get<unknown[]>("/ulpins/sequences", { query: params }),

  issue: (id: string) => api.post<UlpinResponse>(`/ulpins/${id}/issue`),

  supersede: (id: string, body: Record<string, unknown>) =>
    api.post<UlpinResponse>(`/ulpins/${id}/supersede`, body),

  retire: (id: string, reason: string) =>
    api.post<UlpinResponse>(`/ulpins/${id}/retire`, { reason }),
};

/* ------------------------------------------------------------ verification -- */

export const verification = {
  verify: (
    ulpin: string,
    body: {
      claimed_owner_name?: string | null;
      claimed_tenant_name?: string | null;
      remarks?: string | null;
    } = {},
  ) => api.post<VerificationResponse>("/verification/verify", { ulpin, ...body }),

  history: (code: string) =>
    api.get<VerificationRecordResponse[]>(
      `/verification/history/${encodeURIComponent(code)}`,
    ),

  list: (params: { page?: number; page_size?: number; outcome?: string; status?: string } = {}) =>
    api.get<Page<VerificationRecordResponse>>("/verification", { query: params }),

  get: (id: string) => api.get<VerificationRecordResponse>(`/verification/${id}`),

  decide: (
    id: string,
    body: { outcome: string; remarks?: string | null; rejection_reason?: string | null },
  ) => api.post<VerificationRecordResponse>(`/verification/${id}/decide`, body),
};

/* ------------------------------------------------------------------- fraud -- */

export const fraud = {
  scan: (
    body: { building_id?: string | null; rules?: string[] | null; dry_run?: boolean } = {},
  ) => api.post<ScanResponse>("/fraud/scan", body),

  summary: () => api.get<FraudSummaryResponse>("/fraud/summary"),

  trend: (days = 30) => api.get<TrendPoint[]>("/fraud/trend", { query: { days } }),

  alerts: (
    params: {
      page?: number;
      page_size?: number;
      severity?: string;
      status?: string;
      rule_code?: string;
      building_id?: string;
      q?: string;
    } = {},
  ) => api.get<Page<AlertResponse>>("/fraud/alerts", { query: params }),

  alert: (id: string) => api.get<AlertResponse>(`/fraud/alerts/${id}`),

  assign: (id: string, assignee_id: string) =>
    api.post<AlertResponse>(`/fraud/alerts/${id}/assign`, { assignee_id }),

  decide: (
    id: string,
    body: { status: string; resolution_notes?: string | null; is_false_positive?: boolean },
  ) => api.post<AlertResponse>(`/fraud/alerts/${id}/decide`, body),
};

/* ------------------------------------------------------------------ search -- */

/**
 * Names follow `GET /search` exactly. Note `unit_type` here against
 * `property_type` on the unit schemas — the API genuinely uses both names for
 * the same enum on different surfaces, and mirroring rather than normalising
 * keeps a mismatch findable by grep.
 */
export interface SearchParams {
  q: string;
  mode?: SearchMode;
  page?: number;
  page_size?: number;
  unit_type?: string;
  occupancy_status?: string;
  verification_outcome?: string;
  city?: string;
  state?: string;
  building_id?: string;
  floor_number_min?: number;
  floor_number_max?: number;
  has_open_alerts?: boolean;
  // `api.get` takes a `Record<string, QueryValue>`, and a plain interface has no
  // index signature to satisfy it. Declaring one here keeps the named fields
  // documented while letting the whole object be passed as a query.
  [key: string]: string | number | boolean | string[] | null | undefined;
}

export const search = {
  run: (params: SearchParams) => api.get<SearchResponse>("/search", { query: params }),

  suggest: (q: string, limit = 8) =>
    api.get<SuggestResponse>("/search/suggest", { query: { q, limit } }),
};

/* --------------------------------------------------------------- dashboard -- */

export const dashboard = {
  overview: (trend_days = 30) =>
    api.get<DashboardResponse>("/dashboard", { query: { trend_days } }),
};
