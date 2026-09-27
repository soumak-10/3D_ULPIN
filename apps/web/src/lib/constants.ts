/**
 * Option lists for every select in the application.
 *
 * These mirror the PostgreSQL enum types. A value here that the database's type
 * does not hold is a 422 at best and a `22P02 invalid input value for enum` at
 * worst, so the lists are copied from the SQL rather than invented.
 */

export const STATE_CODES = [
  { value: "WB", label: "West Bengal" },
  { value: "KA", label: "Karnataka" },
  { value: "MH", label: "Maharashtra" },
  { value: "DL", label: "Delhi" },
  { value: "TN", label: "Tamil Nadu" },
  { value: "UP", label: "Uttar Pradesh" },
  { value: "GJ", label: "Gujarat" },
  { value: "RJ", label: "Rajasthan" },
  { value: "TS", label: "Telangana" },
  { value: "KL", label: "Kerala" },
  { value: "PB", label: "Punjab" },
  { value: "HR", label: "Haryana" },
  { value: "MP", label: "Madhya Pradesh" },
  { value: "BR", label: "Bihar" },
  { value: "OD", label: "Odisha" },
  { value: "AS", label: "Assam" },
] as const;

export const PROPERTY_TYPES = [
  { value: "RESIDENTIAL", label: "Residential" },
  { value: "COMMERCIAL", label: "Commercial" },
  { value: "INDUSTRIAL", label: "Industrial" },
  { value: "INSTITUTIONAL", label: "Institutional" },
  { value: "MIXED_USE", label: "Mixed use" },
  { value: "PARKING", label: "Parking" },
  { value: "UTILITY", label: "Utility" },
  { value: "COMMON_AREA", label: "Common area" },
] as const;

/** The three the specification names, for forms that should stay narrow. */
export const CORE_PROPERTY_TYPES = PROPERTY_TYPES.slice(0, 3);

export const OCCUPANCY_STATUSES = [
  { value: "OCCUPIED", label: "Occupied" },
  { value: "VACANT", label: "Vacant" },
  { value: "UNDER_RENOVATION", label: "Under renovation" },
  { value: "SEALED", label: "Sealed" },
] as const;

export const VERIFICATION_OUTCOMES = [
  { value: "VERIFIED", label: "Verified" },
  { value: "PENDING_VERIFICATION", label: "Pending verification" },
  { value: "INVALID_CLAIM", label: "Invalid claim" },
  { value: "UNAUTHORIZED_OCCUPANCY", label: "Unauthorized occupancy" },
] as const;

export const STOREY_CLASSES = [
  { value: "BASEMENT", label: "Basement" },
  { value: "STILT", label: "Stilt" },
  { value: "PODIUM", label: "Podium" },
  { value: "GROUND", label: "Ground" },
  { value: "MEZZANINE", label: "Mezzanine" },
  { value: "UPPER", label: "Upper floor" },
  { value: "TERRACE", label: "Terrace" },
  { value: "AIR_RIGHTS", label: "Air rights" },
] as const;

export const ALERT_SEVERITIES = [
  { value: "LOW", label: "Low" },
  { value: "MEDIUM", label: "Medium" },
  { value: "HIGH", label: "High" },
  { value: "CRITICAL", label: "Critical" },
] as const;

export const ALERT_STATUSES = [
  { value: "OPEN", label: "Open" },
  { value: "INVESTIGATING", label: "Investigating" },
  { value: "CONFIRMED", label: "Confirmed" },
  { value: "DISMISSED", label: "Dismissed" },
  { value: "RESOLVED", label: "Resolved" },
] as const;

export const FRAUD_RULES = [
  { value: "MULTIPLE_OWNERS", label: "Multiple owners for same property" },
  { value: "DUPLICATE_ULPIN", label: "Duplicate ULPIN" },
  { value: "OWNERSHIP_MISMATCH", label: "Ownership mismatch" },
  { value: "TENANT_MISMATCH", label: "Tenant mismatch" },
  { value: "UNAUTHORIZED_OCCUPANCY", label: "Unauthorized occupancy" },
] as const;

export const ROLES = [
  { value: "ADMIN", label: "Administrator" },
  { value: "PROPERTY_OFFICER", label: "Property officer" },
  { value: "OWNER", label: "Owner" },
  { value: "TENANT", label: "Tenant" },
] as const;

/** Roles a self-service registration form may offer. */
export const SELF_SERVICE_ROLES = [
  { value: "OWNER", label: "Property owner" },
  { value: "TENANT", label: "Tenant" },
] as const;

export const OWNER_TYPES = [
  { value: "INDIVIDUAL", label: "Individual" },
  { value: "JOINT", label: "Joint" },
  { value: "COMPANY", label: "Company" },
  { value: "TRUST", label: "Trust" },
  { value: "SOCIETY", label: "Co-operative society" },
  { value: "GOVERNMENT", label: "Government" },
  { value: "HUF", label: "Hindu undivided family" },
] as const;

export const OWNERSHIP_MODES = [
  { value: "SOLE", label: "Sole" },
  { value: "JOINT", label: "Joint" },
  { value: "COPARCENARY", label: "Coparcenary" },
  { value: "LEASEHOLD", label: "Leasehold" },
] as const;

export const SEARCH_MODES = [
  { value: "auto", label: "Everything" },
  { value: "ulpin", label: "ULPIN" },
  { value: "owner", label: "Owner name" },
  { value: "tenant", label: "Tenant name" },
  { value: "building", label: "Building name" },
] as const;

export const PAGE_SIZES = [10, 20, 50, 100] as const;

/** Role-visible navigation. Kept next to the roles it references. */
export interface NavItem {
  href: string;
  label: string;
  icon: string;
  roles: readonly string[];
}

const ALL_ROLES = ["ADMIN", "PROPERTY_OFFICER", "OWNER", "TENANT"] as const;
const STAFF = ["ADMIN", "PROPERTY_OFFICER"] as const;

export const NAV_ITEMS: NavItem[] = [
  { href: "/dashboard", label: "Dashboard", icon: "LayoutDashboard", roles: ALL_ROLES },
  { href: "/search", label: "Search", icon: "Search", roles: ALL_ROLES },
  { href: "/viewer", label: "3D viewer", icon: "Box", roles: ALL_ROLES },
  { href: "/buildings", label: "Buildings", icon: "Building2", roles: ALL_ROLES },
  { href: "/ulpins", label: "ULPIN registry", icon: "Fingerprint", roles: STAFF },
  { href: "/verification", label: "Verification", icon: "ShieldCheck", roles: STAFF },
  { href: "/fraud", label: "Fraud alerts", icon: "TriangleAlert", roles: STAFF },
  { href: "/owners", label: "Owners", icon: "Users", roles: STAFF },
];
