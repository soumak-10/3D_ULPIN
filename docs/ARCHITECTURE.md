# 3D ULPIN Generation and Vertical Property Mapping System

**Architecture Specification — v1.0**

> Status: **design only**. No implementation code is included by design.
> Every path referenced is a *target* to be created during scaffolding. File names are
> normative (the implementation should match them) unless marked _(optional)_.

Companion documents:

| Document | Covers |
|---|---|
| [FOLDER-STRUCTURE.md](FOLDER-STRUCTURE.md) | §1 root, §2 backend, §3 frontend, §4 database trees |
| [INFRASTRUCTURE.md](INFRASTRUCTURE.md) | §5 Docker configuration, §6 environment variables |
| [DEPENDENCIES.md](DEPENDENCIES.md) | §7 package dependencies |
| [INSTALLATION.md](INSTALLATION.md) | §8 installation guide |

---

## 0. System Context

### 0.1 Problem statement

A 2D cadastre assigns one **ULPIN** (Unique Land Parcel Identification Number — the
14-character land-parcel identifier used under DILRMP, colloquially *Bhu-Aadhaar*) to a
single planar land parcel. That model breaks down for multi-storey and mixed-use
development, where one footprint carries dozens or hundreds of independently owned
volumes stacked vertically: apartments, parking bays, basements, terraces, utility
shafts, and unbuilt air rights.

This system extends the cadastral record into the third dimension:

1. **Ingest** authoritative 2D cadastral parcels and building footprints.
2. **Reconstruct** each building as a volumetric model (LoD1 → LoD2) from footprint +
   height + floor-plate data, or from supplied CAD / BIM / point-cloud sources.
3. **Subdivide** that volume into legally meaningful **Vertical Property Units (VPUs)**.
4. **Derive** a deterministic **3D ULPIN** for each VPU from its parent 2D ULPIN.
5. **Validate** the partition — no overlapping volumes, no unassigned interior void,
   closed manifold solids, area reconciliation against the sanctioned plan.
6. **Serve** the result as an authoritative API, an interactive 3D map, and
   registry-grade exports.

### 0.2 Actors

| Actor | Responsibilities |
|---|---|
| **Surveyor / Field Officer** | Uploads footprints, floor plates, heights; runs generation jobs |
| **Verifier / Revenue Officer** | Reviews generated VPUs; approves or rejects a building partition |
| **Registrar (SRO)** | Attaches ownership, transactions, encumbrances to an issued 3D ULPIN |
| **Citizen / Owner** | Read-only lookup of own units, certificate download |
| **External System** | Machine-to-machine query (bank, utility, municipal tax, court) |
| **Administrator** | Tenancy, jurisdictions, roles, ULPIN issuance policy, audit review |

### 0.3 Core domain objects

```
Jurisdiction  (state → district → taluk → village)
  └── LandParcel            (2D cadastral parcel, holds the base ULPIN)
        └── Building        (footprint + storeys + height datum)
              └── Storey    (a horizontal slice: basement, ground, typical, terrace)
                    └── VerticalPropertyUnit (VPU)   ← carries the 3D ULPIN
                          ├── Ownership      (share-based, time-versioned)
                          ├── Encumbrance    (mortgage, lien, injunction)
                          └── Transaction    (sale, gift, partition, succession)
```

Supporting objects: `GenerationJob`, `ValidationReport`, `ApprovalWorkflow`,
`DocumentAsset`, `TileSet`, `AuditEvent`, `User`, `Role`, `ApiClient`.

---

## 1. Architectural Overview

### 1.1 Topology

```
                            ┌──────────────────────────┐
     Browser ──────────────▶│  Next.js 15 (App Router) │
                            │  SSR/RSC + Three.js pane │
                            └───────────┬──────────────┘
                                        │ REST/JSON + SSE
                            ┌───────────▼──────────────┐
                            │   Nginx / Traefik edge   │
                            └───────────┬──────────────┘
                                        │
                ┌───────────────────────┼───────────────────────┐
                │                       │                       │
      ┌─────────▼────────┐   ┌──────────▼─────────┐   ┌─────────▼────────┐
      │ FastAPI (ASGI)   │   │ Tile service       │   │ Celery workers   │
      │ api/v1 + auth    │   │ MVT + 3D Tiles     │   │ geometry, tiling │
      └─────────┬────────┘   └──────────┬─────────┘   └─────────┬────────┘
                │                       │                       │
                └───────────┬───────────┴───────────┬───────────┘
                            │                       │
                 ┌──────────▼─────────┐   ┌─────────▼────────┐
                 │ PostgreSQL 16      │   │ Redis 7          │
                 │ + PostGIS 3.4      │   │ broker + cache   │
                 │ + SFCGAL (3D ops)  │   └──────────────────┘
                 └────────────────────┘
                            │
                 ┌──────────▼─────────┐
                 │ MinIO / S3         │
                 │ uploads, glTF, PDF │
                 └────────────────────┘
```

### 1.2 Layering inside the backend

```
HTTP  →  api/v1/endpoints      thin; request/response only
        ↓
         schemas/              Pydantic v2 validation + serialization
        ↓
         services/             business rules, transactions, orchestration
        ↓
         repositories/         SQLAlchemy 2.0 queries, no business logic
        ↓
         models/               ORM + GeoAlchemy2 mappings
        ↓
DB    →  PostGIS
```

Cross-cutting: `core/` (config, security, logging), `geo/` (geometry algorithms —
pure functions, no DB), `ulpin/` (identifier codec — pure, exhaustively unit-tested),
`workers/` (async jobs), `integrations/` (external registries).

**Rule:** `geo/` and `ulpin/` must never import from `models/`, `repositories/`, or
`api/`. They are pure, deterministic, and independently testable. This is the single
most important boundary in the codebase — the ULPIN codec in particular is a legal
artifact and must be provable in isolation.

### 1.3 Request paths

| Path | Flow |
|---|---|
| Parcel lookup | Next.js RSC → FastAPI → repo → PostGIS → JSON |
| 2D map pan | Browser → tile service → `ST_AsMVT` → vector tile (cached in Redis) |
| 3D scene load | Browser → CDN/S3 → 3D Tiles tileset.json → glTF chunks → Three.js |
| Generation run | UI → FastAPI enqueue → Celery → PostGIS/SFCGAL → tiles → SSE progress |
| Certificate | UI → FastAPI → WeasyPrint → S3 signed URL |

---

## 2. The 3D ULPIN Identifier

### 2.1 Design constraints

- **Derivable, not allocated** — a VPU's identifier is a pure function of its parent
  ULPIN and its position in the building. Regenerating must reproduce it exactly.
- **Backward compatible** — the leading 14 characters are the unmodified parent ULPIN,
  so any existing 2D system can still parse the prefix.
- **Human-transcribable** — Crockford Base32 alphabet (no `I`, `L`, `O`, `U`), so
  ambiguous glyph pairs cannot be confused on a printed deed.
- **Self-checking** — a trailing check character detects all single-character errors
  and all adjacent transpositions.
- **Stable under renumbering** — derived from geometry ordinals, not from insertion order.

### 2.2 Proposed structure

```
  IN29BLR000123 4 - B1 - F007 - U0412 - K
  └────┬───────┘ │   └┬┘  └─┬─┘  └─┬─┘  │
       │         │    │     │      │    └── check char (1)  mod-37,36 over all above
       │         │    │     │      └─────── unit ordinal (4) U + base32 sequence in storey
       │         │    │     └────────────── storey code  (4) F/B/M/T + signed level
       │         │    └──────────────────── block code   (2) building within parcel
       │         └───────────────────────── existing 2D ULPIN check digit
       └─────────────────────────────────── existing 2D ULPIN body (13)
```

Total: 14 (parent) + 1 + 2 + 1 + 4 + 1 + 5 + 1 + 1 = **30 characters** with separators,
**25** without. Storage is the canonical unseparated form; display inserts hyphens.

### 2.3 Storey codes

| Prefix | Meaning | Example |
|---|---|---|
| `B` | Basement, counting down | `B001` = first basement, `B003` = third |
| `G` | Ground / plinth level | `G000` |
| `M` | Mezzanine attached to the storey below | `M001` |
| `F` | Upper floor | `F007` = seventh floor |
| `T` | Terrace / roof-level rights | `T000` |
| `A` | Air-rights volume above the built envelope | `A001` |

Numbering follows the *sanctioned plan*, not the physical count — a building that skips
floor 13 keeps the gap. The mapping from sanctioned label to storey code is stored on
`storeys.label` so the original is never lost.

### 2.4 Unit ordinal assignment

Deterministic spatial sort within each storey, so a re-run is reproducible:

1. Project the unit's floor-plate centroid to the jurisdiction's metric CRS.
2. Sort by **Hilbert curve index** over the storey's bounding box (stable, locality-preserving).
3. Ties (identical centroids, which indicates bad input) break by `ST_Area` descending,
   then by source-file feature ID.

Because the sort key is geometric, adding a unit later renumbers its neighbours — so
**issued identifiers are frozen on approval**. Post-approval additions take the next
free ordinal at the end of the sequence and are recorded as such.

### 2.5 Lifecycle

```
 DRAFT ──generate──▶ PROVISIONAL ──validate──▶ VERIFIED ──approve──▶ ISSUED
                           │                       │                    │
                           └───── reject ──────────┘                    ├─▶ SUPERSEDED
                                                                        │   (amalgamation/
                                                                        │    subdivision)
                                                                        └─▶ RETIRED
                                                                            (demolition)
```

An **ISSUED** identifier is never deleted and never reused. Subdivision mints children
and marks the parent `SUPERSEDED` with a lineage edge in `ulpin_lineage`.

---

## 3. Geometry & 3D Pipeline

### 3.1 Coordinate reference systems

| Purpose | CRS | Notes |
|---|---|---|
| Canonical storage | **EPSG:4326** (WGS84, lon/lat, Z in metres) | interchange + API |
| Area / distance | **EPSG:7755** (WGS84 / India NSF LCC) or state UTM (32642–32647) | per-jurisdiction, set in `jurisdictions.metric_srid` |
| Vertical datum | **EGM2008 geoid → orthometric (MSL) metres** | store the datum id on every Z-bearing row |
| Local 3D scene | ENU tangent plane at the building anchor | computed at render time, never stored |

Storing area-derived values (`area_sqm`, `volume_cum`) in metric CRS while keeping
geometry in 4326 is deliberate: geometry stays interchange-ready, and measurements stay
legally defensible. Never compute area directly on 4326 degrees.

### 3.2 Geometry column plan

| Table | Column | Type |
|---|---|---|
| `land_parcels` | `boundary` | `GEOMETRY(PolygonZ, 4326)` — Z may be null-plane |
| `buildings` | `footprint` | `GEOMETRY(PolygonZ, 4326)` |
| `buildings` | `envelope_solid` | `GEOMETRY(PolyhedralSurfaceZ, 4326)` |
| `storeys` | `slab_polygon` | `GEOMETRY(PolygonZ, 4326)` |
| `vertical_property_units` | `floor_plate` | `GEOMETRY(PolygonZ, 4326)` |
| `vertical_property_units` | `volume_solid` | `GEOMETRY(PolyhedralSurfaceZ, 4326)` |
| `vertical_property_units` | `centroid_3d` | `GEOMETRY(PointZ, 4326)` |

Indexes: GiST on all geometry columns; additionally **ND-GiST**
(`USING gist (volume_solid gist_geometry_ops_nd)`) on `volume_solid` for true 3D
bounding-box search.

### 3.3 Generation pipeline stages

```
 1. INGEST      parse GeoJSON/Shapefile/DXF/IFC → staging tables, CRS normalised
 2. CLEAN       ST_MakeValid, ST_RemoveRepeatedPoints, sliver removal, snap to grid
 3. MATCH       associate footprint → parent land parcel (ST_Within / ST_Overlaps + share)
 4. EXTRUDE     footprint + storey heights → PolyhedralSurfaceZ per storey (LoD1)
 5. PARTITION   floor plates → per-unit prisms; carve common areas and shafts
 6. IDENTIFY    assign block/storey/unit ordinals → mint provisional 3D ULPINs
 7. VALIDATE    solidity, overlap, containment, closure, area reconciliation
 8. TESSELLATE  solids → triangle meshes → glTF → 3D Tiles 1.1 tileset
 9. PUBLISH     write tileset to object store, flip job to awaiting-approval
```

Stages 4–8 run in Celery workers. Every stage writes a `ValidationReport` fragment, so a
failure is attributable to a stage and a feature, not just "job failed".

### 3.4 Validation rules (blocking unless noted)

| Code | Rule |
|---|---|
| `GEO-001` | Every VPU solid is closed and manifold (`ST_IsSolid` via SFCGAL) |
| `GEO-002` | No two VPU solids in a building intersect beyond tolerance (`ST_3DIntersection` volume ≤ ε) |
| `GEO-003` | Every VPU is contained in its building envelope |
| `GEO-004` | Storey slabs are vertically ordered and non-overlapping |
| `GEO-005` | Sum(unit + common area) reconciles to the storey plate ± tolerance |
| `ULP-001` | Every minted identifier passes its own check character |
| `ULP-002` | No collision against issued identifiers, globally |
| `ULP-003` | Parent prefix matches the parcel's registered 2D ULPIN |
| `DOC-001` | Sanctioned-plan document attached _(warning only)_ |

Tolerance ε is per-jurisdiction (`jurisdictions.geom_tolerance_m`, default 0.01 m).

### 3.5 Three.js rendering strategy

- **Streaming:** 3D Tiles 1.1 with implicit tiling; glTF 2.0 + Draco for geometry,
  KTX2/Basis for textures. The browser never receives raw PostGIS solids.
- **Picking:** GPU colour-ID pass rather than raycasting the full BVH — constant cost
  regardless of unit count.
- **Sectioning:** clipping planes for storey-by-storey "explode" and cut-away views.
- **Levels of detail:** LoD1 (mass) beyond ~500 m, LoD2 (per-unit) inside.
- **Overlay:** the 2D cadastre renders as a MapLibre basemap; the Three.js canvas shares
  its camera matrix so 2D and 3D stay locked. Both live behind one `SceneProvider`.
- **Budget:** target 60 fps with 5 000 visible units on integrated graphics — enforced by
  instanced meshes per storey plus frustum + occlusion culling.

---

## 4. Security, Audit & Multi-Tenancy

| Concern | Approach |
|---|---|
| AuthN | OIDC (Keycloak or state SSO); JWT access + rotating refresh; optional mTLS for M2M |
| AuthZ | RBAC × jurisdiction scope, enforced in `services/`, mirrored by PostgreSQL RLS |
| Tenancy | Single database, `jurisdiction_id` on every business row, RLS policy per role |
| Audit | Append-only `audit_events`; every ULPIN state change writes a row with actor, before/after hash |
| Immutability | Issued identifiers + approved geometry are write-protected by trigger |
| PII | Owner identity fields encrypted at column level (pgcrypto); Aadhaar never stored, only a hashed token |
| Uploads | Type + magic-byte sniffing, size cap, out-of-band virus scan, quarantine bucket |
| Rate limits | Per-client token bucket in Redis at the edge and in middleware |
| Transport | TLS 1.3 only; HSTS; strict CSP with a nonce for the WebGL bundle |

---

## 5. Non-Functional Targets

| Metric | Target |
|---|---|
| API p95 (read) | < 200 ms |
| Tile p95 (cached) | < 50 ms |
| 3D first meaningful paint | < 2.5 s on 10 Mbit |
| Generation throughput | 1 000 VPUs per building-job in < 90 s |
| Availability | 99.5 % business hours |
| RPO / RTO | 15 min / 1 h (WAL archiving + PITR) |
| Max scale (phase 1) | 50 M parcels, 5 M buildings, 200 M VPUs |

---

## 6. Build Order

Recommended sequence once scaffolding exists — each phase ends with something demonstrable.

| Phase | Deliverable |
|---|---|
| 0 | Repo scaffold, Docker Compose up, health check green, CI running |
| 1 | DB schema + migrations + seed jurisdiction; parcel CRUD; 2D map |
| 2 | ULPIN codec (encode/decode/checksum) with exhaustive property tests |
| 3 | Building + storey ingest; LoD1 extrusion; solids in PostGIS |
| 4 | Unit partition + identifier minting + validation report |
| 5 | Tiling pipeline + Three.js viewer + picking + section planes |
| 6 | Approval workflow, audit trail, certificate PDF |
| 7 | Ownership, encumbrance, transaction ledger |
| 8 | Public read API, rate limiting, external-system integration |
| 9 | Observability, load testing, security review, deployment hardening |

Phase 2 before phase 3 is deliberate: the identifier scheme is the contract the rest of
the system is built against, and it is far cheaper to change before data exists.
