import { z } from "zod";

/**
 * Client-side schemas.
 *
 * These duplicate the server's Pydantic validation deliberately — the server's
 * is the one that counts, this one exists so a typo is caught before a round
 * trip. Where the two could drift, the message here says what the rule is
 * rather than restating a regex, because a user reading "does not match
 * ^[A-Z]{2}-..." learns nothing.
 */

/* -------------------------------------------------------------------- auth -- */

const password = z
  .string()
  .min(12, "Use at least 12 characters.")
  .max(128, "That is longer than 128 characters.")
  .refine((v) => /[a-z]/.test(v), "Include a lower-case letter.")
  .refine((v) => /[A-Z]/.test(v), "Include an upper-case letter.")
  .refine((v) => /[0-9]/.test(v), "Include a digit.");

export const loginSchema = z.object({
  email: z.string().min(1, "Enter your email address.").email("That is not a valid email address."),
  password: z.string().min(1, "Enter your password."),
});
export type LoginValues = z.infer<typeof loginSchema>;

export const registerSchema = z
  .object({
    full_name: z
      .string()
      .min(2, "Enter your full name as it appears on your records.")
      .max(160),
    email: z.string().email("That is not a valid email address."),
    // Ten digits, optionally +91-prefixed. Deliberately not stricter: the
    // register holds numbers captured over twenty years in a dozen formats.
    phone: z
      .string()
      .regex(/^(\+91[-\s]?)?[6-9]\d{9}$/, "Enter a 10-digit Indian mobile number.")
      .optional()
      .or(z.literal("")),
    role: z.enum(["OWNER", "TENANT"]).default("OWNER"),
    password,
    confirm_password: z.string(),
  })
  .refine((v) => v.password === v.confirm_password, {
    path: ["confirm_password"],
    message: "The two passwords do not match.",
  });
export type RegisterValues = z.infer<typeof registerSchema>;

export const forgotPasswordSchema = z.object({
  email: z.string().email("That is not a valid email address."),
});
export type ForgotPasswordValues = z.infer<typeof forgotPasswordSchema>;

/**
 * Six digits. Separators are stripped before validating because people paste
 * "123 456" straight out of the email — telling them their correct code is
 * malformed would be the form's fault, not theirs. The server strips them too.
 */
export const otpCodeField = z
  .string()
  .transform((v) => v.replace(/[\s-]/g, ""))
  .pipe(
    z
      .string()
      .length(6, "The code is six digits.")
      .regex(/^\d{6}$/, "The code is six digits."),
  );

export const verifyOtpSchema = z.object({
  email: z.string().email("That is not a valid email address."),
  code: otpCodeField,
});
export type VerifyOtpValues = z.infer<typeof verifyOtpSchema>;

export const resetPasswordSchema = z
  .object({
    token: z.string().min(1, "The reset link is incomplete."),
    password,
    confirm_password: z.string(),
  })
  .refine((v) => v.password === v.confirm_password, {
    path: ["confirm_password"],
    message: "The two passwords do not match.",
  });
export type ResetPasswordValues = z.infer<typeof resetPasswordSchema>;

export const changePasswordSchema = z
  .object({
    current_password: z.string().min(1, "Enter your current password."),
    new_password: password,
    confirm_password: z.string(),
  })
  .refine((v) => v.new_password === v.confirm_password, {
    path: ["confirm_password"],
    message: "The two passwords do not match.",
  });
export type ChangePasswordValues = z.infer<typeof changePasswordSchema>;

/* ---------------------------------------------------------------- property -- */

const optionalNumber = (schema: z.ZodNumber) =>
  z.preprocess(
    (v) => (v === "" || v === null || v === undefined ? undefined : Number(v)),
    schema.optional(),
  );

export const buildingSchema = z.object({
  building_name: z.string().min(3, "Enter the building's name.").max(200),
  address_line1: z.string().min(5, "Enter the street address.").max(255),
  address_line2: z.string().max(255).optional().or(z.literal("")),
  locality: z.string().max(120).optional().or(z.literal("")),
  city: z.string().min(2, "Enter the city.").max(120),
  district: z.string().max(120).optional().or(z.literal("")),
  state_code: z.string().length(2, "Select a state."),
  pincode: z
    .string()
    .regex(/^[1-9]\d{5}$/, "A PIN code is six digits and does not start with 0.")
    .optional()
    .or(z.literal("")),
  // India spans roughly 6.5°–37.5°N and 68°–97.5°E. Bounding the inputs stops a
  // transposed pair (longitude typed into latitude) reaching the database,
  // where it would place a Kolkata tower in the Bay of Bengal.
  latitude: z.coerce
    .number({ invalid_type_error: "Enter a latitude." })
    .min(6, "That latitude is outside India.")
    .max(38, "That latitude is outside India."),
  longitude: z.coerce
    .number({ invalid_type_error: "Enter a longitude." })
    .min(67, "That longitude is outside India.")
    .max(98, "That longitude is outside India."),
  total_floors: z.coerce
    .number({ invalid_type_error: "Enter the number of floors." })
    .int("Floors are whole numbers.")
    .min(1, "A building has at least one floor.")
    .max(200, "More than 200 floors needs a manual entry."),
  basement_floors: z.coerce.number().int().min(0).max(20).default(0),
  building_height_m: optionalNumber(z.number().positive().max(1000)),
  year_built: optionalNumber(z.number().int().min(1800).max(2100)),
  generate_floors: z.boolean().default(true),
});
export type BuildingValues = z.infer<typeof buildingSchema>;

export const unitSchema = z.object({
  floor_number: z.coerce
    .number({ invalid_type_error: "Enter the floor number." })
    .int("Floor numbers are whole numbers.")
    .min(-20)
    .max(200),
  unit_number: z
    .string()
    .min(1, "Enter the unit number.")
    .max(32)
    .regex(/^[A-Za-z0-9][A-Za-z0-9\-/ ]*$/, "Use letters, digits, hyphens and slashes only."),
  property_type: z.enum([
    "RESIDENTIAL",
    "COMMERCIAL",
    "INDUSTRIAL",
    "INSTITUTIONAL",
    "MIXED_USE",
    "PARKING",
    "UTILITY",
    "COMMON_AREA",
  ]),
  occupancy_status: z.enum(["OCCUPIED", "VACANT", "UNDER_RENOVATION", "SEALED"]).default("VACANT"),
  carpet_area_sqm: optionalNumber(z.number().positive().max(100000)),
  built_up_area_sqm: optionalNumber(z.number().positive().max(100000)),
  generate_ulpin: z.boolean().default(true),
});
export type UnitValues = z.infer<typeof unitSchema>;

export const ownerSchema = z
  .object({
    owner_type: z
      .enum(["INDIVIDUAL", "JOINT", "COMPANY", "TRUST", "SOCIETY", "GOVERNMENT", "HUF"])
      .default("INDIVIDUAL"),
    full_name: z.string().max(160).optional().or(z.literal("")),
    organisation_name: z.string().max(200).optional().or(z.literal("")),
    email: z.string().email("That is not a valid email address.").optional().or(z.literal("")),
    phone: z
      .string()
      .regex(/^(\+91[-\s]?)?[6-9]\d{9}$/, "Enter a 10-digit Indian mobile number.")
      .optional()
      .or(z.literal("")),
    address_line1: z.string().max(255).optional().or(z.literal("")),
    city: z.string().max(120).optional().or(z.literal("")),
    state_code: z.string().length(2).optional().or(z.literal("")),
    pincode: z.string().regex(/^[1-9]\d{5}$/, "Six digits.").optional().or(z.literal("")),
    pan: z
      .string()
      .regex(/^[A-Z]{5}[0-9]{4}[A-Z]$/, "A PAN looks like ABCDE1234F.")
      .optional()
      .or(z.literal("")),
  })
  // An individual owner must have a person's name; an entity must have the
  // entity's. Requiring both would block half the register, requiring neither
  // would admit an owner nobody can identify.
  .refine(
    (v) =>
      ["COMPANY", "TRUST", "SOCIETY", "GOVERNMENT"].includes(v.owner_type)
        ? Boolean(v.organisation_name)
        : Boolean(v.full_name),
    {
      path: ["full_name"],
      message: "Enter the owner's name (or the organisation's, for an entity).",
    },
  );
export type OwnerValues = z.infer<typeof ownerSchema>;

export const ownershipSchema = z.object({
  owner_id: z.string().uuid("Select an owner."),
  ownership_mode: z.enum(["SOLE", "JOINT", "COPARCENARY", "LEASEHOLD"]).default("SOLE"),
  share_fraction: z.coerce
    .number({ invalid_type_error: "Enter a share." })
    .gt(0, "A share must be greater than zero.")
    .max(1, "A share cannot exceed the whole property.")
    .default(1),
  is_primary: z.boolean().default(true),
  acquired_on: z.string().min(1, "Enter the date of acquisition."),
  deed_number: z.string().max(64).optional().or(z.literal("")),
  deed_date: z.string().optional().or(z.literal("")),
  acquisition_mode: z.string().max(40).optional().or(z.literal("")),
  consideration_amount: optionalNumber(z.number().nonnegative()),
});
export type OwnershipValues = z.infer<typeof ownershipSchema>;

export const tenantSchema = z
  .object({
    full_name: z.string().min(2, "Enter the tenant's full name.").max(160),
    email: z.string().email("That is not a valid email address.").optional().or(z.literal("")),
    phone: z
      .string()
      .regex(/^(\+91[-\s]?)?[6-9]\d{9}$/, "Enter a 10-digit Indian mobile number.")
      .optional()
      .or(z.literal("")),
    lease_start: z.string().min(1, "Enter the lease start date."),
    lease_end: z.string().optional().or(z.literal("")),
    monthly_rent: optionalNumber(z.number().nonnegative().max(100000000)),
    deposit_amount: optionalNumber(z.number().nonnegative().max(1000000000)),
    agreement_number: z.string().max(64).optional().or(z.literal("")),
  })
  .refine((v) => !v.lease_end || v.lease_end >= v.lease_start, {
    path: ["lease_end"],
    message: "A lease cannot end before it begins.",
  });
export type TenantValues = z.infer<typeof tenantSchema>;

/* ------------------------------------------------------- ulpin / verify -- */

/**
 * The short, quotable form: WB-KOL-B001-F03-U301.
 *
 * Storey-class letters distinguish basement 3 from floor 3, which a bare number
 * cannot. B basement, G ground, M mezzanine, F upper, T terrace, A air rights,
 * S stilt, P podium.
 */
export const SHORT_CODE_RE = /^[A-Z]{2}-[A-Z]{3}-B\d{3,4}-[BGMFTASP]\d{2,3}-U\d{3,4}$/;

/** The machine-facing long form, which carries the 14-char parcel ULPIN. */
export const LONG_CODE_RE = /^[A-Z0-9]{14}-[A-Z0-9]+-[BGMFTASP]\d{3}-U\d{4}-[A-Z0-9]$/;

export const verifySchema = z.object({
  ulpin: z
    .string()
    .min(1, "Enter a ULPIN.")
    .transform((v) => v.trim().toUpperCase())
    .refine(
      (v) => SHORT_CODE_RE.test(v) || LONG_CODE_RE.test(v),
      "That is not a ULPIN. The short form looks like WB-KOL-B001-F03-U301.",
    ),
  claimed_owner_name: z.string().max(160).optional().or(z.literal("")),
  claimed_tenant_name: z.string().max(160).optional().or(z.literal("")),
  remarks: z.string().max(1000).optional().or(z.literal("")),
});
export type VerifyValues = z.infer<typeof verifySchema>;

export const alertDecisionSchema = z.object({
  status: z.enum(["INVESTIGATING", "CONFIRMED", "DISMISSED", "RESOLVED"]),
  resolution_notes: z
    .string()
    .max(2000)
    .optional()
    .or(z.literal("")),
  is_false_positive: z.boolean().default(false),
});
export type AlertDecisionValues = z.infer<typeof alertDecisionSchema>;

export const verificationDecisionSchema = z.object({
  outcome: z.enum([
    "VERIFIED",
    "PENDING_VERIFICATION",
    "INVALID_CLAIM",
    "UNAUTHORIZED_OCCUPANCY",
  ]),
  remarks: z.string().max(2000).optional().or(z.literal("")),
  rejection_reason: z.string().max(500).optional().or(z.literal("")),
});
export type VerificationDecisionValues = z.infer<typeof verificationDecisionSchema>;

/** Strips "" back to undefined so an empty optional field is omitted, not sent. */
export function clean<T extends Record<string, unknown>>(values: T): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(values)) {
    if (v === "" || v === undefined) continue;
    out[k] = v;
  }
  return out;
}
