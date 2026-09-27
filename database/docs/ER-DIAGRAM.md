# ER Diagram — Explanation

Schema: `ulpin` · Engine: PostgreSQL 16 + PostGIS 3.4 + SFCGAL
DDL: [`00_extensions.sql`](../schemas/00_extensions.sql) → [`01_types.sql`](../schemas/01_types.sql) → [`02_tables.sql`](../schemas/02_tables.sql) → [`03_indexes.sql`](../schemas/03_indexes.sql) → [`04_triggers.sql`](../schemas/04_triggers.sql) → [`01_seed.sql`](../seeds/01_seed.sql)

---

## 1. The shape of the model

```
                          ┌───────────┐
                          │   users   │  auth subjects (4 roles + auditor/service)
                          └─────┬─────┘
                       ┌────────┼────────────────┬──────────────┐
                       │        │                │              │
                       ▼        ▼                ▼              ▼
              ┌────────────┐  ┌────────┐   ┌──────────┐  ┌─────────────┐
              │refresh_tok.│  │ owners │   │ tenants  │  │ audit_logs  │
              └────────────┘  └───┬────┘   └────┬─────┘  └─────────────┘
                                  │             │         (actor_user_id)
                                  │             │
   ┌───────────┐  1        N  ┌───┴────────┐    │
   │ buildings │──────────────│   floors   │    │
   └─────┬─────┘              └─────┬──────┘    │
         │                     1    │    N      │
         │                          ▼           │
         │                    ┌───────────┐     │
         └───────────────────▶│   units   │◀────┘  (tenants.unit_id)
              (denormalised,  └─────┬─────┘
               FK-verified)         │
                    ┌───────────────┼────────────────┐
                    │               │                │
                    ▼               ▼                ▼
          ┌──────────────────┐ ┌─────────┐  ┌────────────────┐
          │ unit_ownerships  │ │ ulpins  │  │  fraud_alerts  │
          │  (M:N with       │ │ 1:1 live│  │                │
          │   owners)        │ └────┬────┘  └────────────────┘
          └──────────────────┘      │              ▲
                                    ▼              │
                       ┌────────────────────────┐  │
                       │ verification_records   │──┘
                       └────────────────────────┘
```

The spine is **buildings → floors → units**: a strict containment hierarchy, one
parent each, cascade on delete. Everything else hangs off `units` or off the
identifier minted for a unit.

---

## 2. Relationship inventory

| From | To | Cardinality | Delete rule | Why |
|---|---|---|---|---|
| `refresh_tokens` | `users` | N:1 | CASCADE | Sessions die with the account |
| `refresh_tokens` | `refresh_tokens` | N:1 self | SET NULL | Rotation chain (`replaced_by`) |
| `owners` | `users` | N:1 optional | SET NULL | Most legacy owners have no login |
| `buildings` | `users` ×2 | N:1 optional | SET NULL | `created_by`, `verified_by` — history outlives staff |
| `buildings` | `vertical_datums` | N:1 | RESTRICT | Every Z value needs a named datum |
| `floors` | `buildings` | N:1 | CASCADE | A floor has no meaning without its building |
| `units` | `floors` | N:1 | CASCADE | |
| `units` | `buildings` | N:1 | CASCADE | Denormalised, see §3 |
| `units` | `(floors.floor_id, building_id)` | composite FK | CASCADE | Proves the denormalisation is honest |
| `unit_ownerships` | `units` | N:1 | CASCADE | |
| `unit_ownerships` | `owners` | N:1 | **RESTRICT** | An owner with holdings cannot be erased |
| `tenants` | `units` | N:1 | CASCADE | |
| `tenants` | `owners` | N:1 optional | SET NULL | Landlord who granted the tenancy |
| `ulpins` | `buildings`/`floors`/`units` | N:1 each, nullable | CASCADE | Subject depends on `ulpin_type` |
| `ulpins` | `ulpins` ×2 self | N:1 | SET NULL | `parent_ulpin_id` (lineage), `supersedes_id` |
| `verification_records` | `ulpins` | N:1 | CASCADE | Verification is always *of* an identifier |
| `verification_records` | `units`/`buildings`/`owners`/`users` | N:1 optional | SET NULL | Context, not ownership |
| `fraud_alerts` | 6 subjects | N:1 optional | CASCADE on `ulpin_id`, SET NULL elsewhere | An alert names whatever it found |
| `audit_logs` | `users` | N:1 optional | SET NULL | System actions have no actor |

### The one many-to-many

`units ↔ owners` through **`unit_ownerships`**. This table is not in the original
ten, and it is the one addition the model genuinely requires:

- A flat held jointly by spouses is two rows against one unit.
- One person owning three flats is three rows against one owner.

Hanging `unit_id` directly off `owners` would force a duplicate owner row per
property, which destroys identity resolution — and identity resolution is exactly
what `IDENTITY_MISMATCH` and `OWNERSHIP_CONFLICT` detection depends on. The seed
demonstrates the failure mode it prevents: two owner rows sharing one
`national_id_hash` raise a HIGH alert.

`tenants` deliberately is *not* modelled the same way. A tenancy is inherently
time-bounded and unit-scoped, so one row per agreement is the natural grain; a
person renting two units produces two rows, which is how lease registries work.

---

## 3. Deliberate design choices

**Denormalised `units.building_id`.** Reaching a building from a unit would
otherwise need a join through `floors` on every tile query and every spatial
filter. The risk of a denormalised key is drift, so it is closed off with a
composite foreign key against `floors(floor_id, building_id)` — backed by
`uq_floors_identity`. An inconsistent `building_id` is not merely discouraged, it
is unrepresentable.

**`ulpins` as a separate table, not a column on `units`.** Identifiers outlive
the rows they describe. A superseded identifier must remain resolvable forever,
lineage must be traversable, and one parcel's identifier has no unit at all.
`uq_ulpins_live_unit` (partial unique index) enforces that a unit has at most one
*live* identifier while permitting any number of historical ones.

**Segments stored alongside the code.** `ulpins` keeps both `ulpin_code` and its
decomposition (`parent_ulpin`, `block_code`, `storey_code`, `unit_code`,
`check_char`). The code is the artefact on the deed; the segments exist so
"every unit on storey F007" is an index scan rather than a substring predicate.
`trg_ulpin_segments` derives segments from the code on every write, so they cannot
disagree.

**Checksum enforced in the database.** `ck_ulpins_checksum` calls
`fn_ulpin_verify()`. A malformed identifier cannot be stored even if the API is
wrong, a migration is hand-run, or someone reaches the database directly. The same
ISO 7064 MOD 36,36 algorithm exists in Python and TypeScript; all three read
`tests/fixtures/ulpin-vectors.json`, and divergence fails CI.

**Areas as `numeric`, shares as `numeric(12,9)`.** Never float. Active ownership
shares must sum to exactly `1`, checked by a `DEFERRABLE INITIALLY DEFERRED`
constraint trigger — deferred because a transfer legitimately passes through an
invalid intermediate state inside one transaction.

**`audit_logs` partitioned and append-only.** `UPDATE`/`DELETE`/`TRUNCATE` are
revoked from every application role. Each row hashes its payload together with the
previous row's hash, so a retroactive edit breaks the chain from that point on;
`fn_audit_verify_chain()` locates the break. Monthly range partitions make
retention a `DETACH PARTITION` rather than a mass delete.

---

## 4. Spatial columns and their indexes

| Table | Column | Type | Index |
|---|---|---|---|
| `owners` | `address_location` | `Point, 4326` | GiST (partial) |
| `buildings` | `footprint` | `PolygonZ, 4326` | GiST |
| `buildings` | `location` | `Point, 4326` | GiST (partial) |
| `buildings` | `envelope_solid` | `PolyhedralSurfaceZ, 4326` | **GiST `_nd`** |
| `floors` | `slab_geom` | `PolygonZ, 4326` | GiST |
| `floors` | `slab_solid` | `PolyhedralSurfaceZ, 4326` | **GiST `_nd`** |
| `units` | `floor_plate` | `PolygonZ, 4326` | GiST |
| `units` | `volume_solid` | `PolyhedralSurfaceZ, 4326` | **GiST `_nd`** + GiST 2D |
| `units` | `centroid_3d` | `PointZ, 4326` | GiST (partial) |
| `ulpins` | `centroid` | `PointZ, 4326` | GiST (partial) |
| `verification_records` | `captured_location` | `Point, 4326` | GiST (partial) |
| `fraud_alerts` | `alert_location` | `Point, 4326` | GiST (partial) |

### Why `gist_geometry_ops_nd` matters

The default GiST operator class indexes the **2D** bounding box only. In a tower,
every unit shares roughly the same X/Y extent — so a 2D index returns the entire
building for any overlap probe and the vertical dimension is filtered by recheck.
The `_nd` operator class indexes the full n-dimensional box, which is what makes
the `&&&` operator index-assisted:

```sql
-- Index-assisted 3D overlap. &&& narrows by n-D bounding box,
-- ST_3DIntersects does the exact test on the survivors.
SELECT a.unit_id, b.unit_id
FROM units a
JOIN units b
  ON a.building_id = b.building_id
 AND a.unit_id < b.unit_id
 AND a.volume_solid &&& b.volume_solid          -- idx_units_volume_nd
WHERE ST_3DIntersects(a.volume_solid, b.volume_solid);
```

Both index types are kept on `units.volume_solid`: map-extent queries never touch
Z, and for those the 2D operator class is the cheaper plan.

### The measurement rule

Geometry is stored in **EPSG:4326** with Z in **metres above EGM2008**. These are
mixed-unit coordinates, so measuring on them directly is meaningless —
`ST_Area` would return square degrees. All measurement goes through
`fn_area_sqm()` and `fn_volume_cum()`, which transform to the jurisdiction's
metric SRID (`buildings.metric_srid`, default 7755) first. Making the transform
part of the function signature is what keeps a wrong answer from being convenient.

---

## 5. Constraint highlights

| Constraint | Table | Protects against |
|---|---|---|
| `ck_floors_code_num` | `floors` | Storey code drifting from the floor it names |
| `ck_floors_type_num` | `floors` | A `BASEMENT` typed at floor `+3` |
| `ck_units_areas` | `units` | carpet > built-up > super-built-up inversions |
| `ck_units_elev` | `units` | Zero-height or inverted volumes |
| `ex_tenants_no_overlap` | `tenants` | Two active tenancies on one unit in one period |
| `uq_ownership_active` | `unit_ownerships` | Duplicate live holdings for one owner |
| `ck_ulpins_subject` | `ulpins` | A `UNIT_3D` identifier with no unit |
| `ck_ulpins_prefix` | `ulpins` | Identifier claiming a parcel it does not sit on |
| `ck_fraud_subject` | `fraud_alerts` | An alert pointing at nothing |
| `ck_verif_rejection` | `verification_records` | Rejection with no stated reason |

`ex_tenants_no_overlap` uses `btree_gist` so a `uuid` equality and a `daterange`
overlap can share one exclusion constraint — the extension exists in
`00_extensions.sql` specifically for this.

---

## 6. Lifecycle enforced in the database

```
ULPIN:   DRAFT → PROVISIONAL → VERIFIED → ISSUED → SUSPENDED
                                             ├──→ SUPERSEDED
                                             └──→ RETIRED
```

`trg_ulpin_status` rejects any transition outside this graph, requires a reason to
retire, and refuses to issue a unit that carries an unresolved HIGH or CRITICAL
fraud alert. Once ISSUED, `trg_units_protect` freezes the unit's geometry and
areas — corrections mint a new identifier and mark the old one SUPERSEDED, leaving
the lineage traversable through `supersedes_id`.

---

## 7. Row counts after seeding

| Table | Rows |
|---|---|
| `users` | 5 (one per role) |
| `owners` | 5 (including one deliberate identity collision) |
| `buildings` | 1 |
| `floors` | 10 (B2, B1, G, 1–7) |
| `units` | 32 (2 parking, 2 shops, 28 apartments) |
| `unit_ownerships` | 33 (one unit jointly held 60/40) |
| `tenants` | 3 (1 active residential, 1 expired, 1 active retail) |
| `ulpins` | 32, all ISSUED |
| `verification_records` | 34 (32 geometric, 1 field, 1 rejected document) |
| `fraud_alerts` | 3 (HIGH investigating, MEDIUM open, LOW dismissed) |
| `audit_logs` | ~150, hash-chained |

Sample identifier: `IN29BLR0001234-A1-F004-U0401-<check>` — the check character is
computed by `fn_ulpin_checksum()` during seeding and never typed by hand.
