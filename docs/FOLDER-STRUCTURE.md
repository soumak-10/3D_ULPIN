# Folder Structure

Covers deliverables **§1 complete structure**, **§2 backend**, **§3 frontend**, **§4 database**.

Legend: `*` = generated/ignored by git · `+` = contains secrets, never committed ·
_(opt)_ = optional for phase 1.

---

## 1. Repository Root

Monorepo, `pnpm` workspaces for JS, `uv`/`pip-tools` for Python. Single `docker-compose`
brings the whole thing up.

```
ulpin-3d-system/
├── .github/
│   ├── workflows/
│   │   ├── ci-backend.yml            lint, mypy, pytest, coverage gate
│   │   ├── ci-frontend.yml           lint, tsc, vitest, playwright
│   │   ├── ci-database.yml           migration up/down round-trip on fresh PostGIS
│   │   ├── codeql.yml                static analysis
│   │   ├── docker-publish.yml        build + push images on tag
│   │   └── deploy-staging.yml        gated deploy
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md
│   │   ├── feature_request.md
│   │   └── data_defect.md            cadastral/geometry data issues
│   ├── PULL_REQUEST_TEMPLATE.md
│   ├── CODEOWNERS
│   └── dependabot.yml
│
├── apps/
│   ├── api/                          FastAPI service          → §2
│   ├── web/                          Next.js 15 application   → §3
│   ├── worker/                       Celery workers (shares api package)
│   │   ├── Dockerfile
│   │   ├── celery_app.py
│   │   └── README.md
│   └── tiler/                        (opt) standalone MVT / 3D Tiles service
│       ├── Dockerfile
│       └── README.md
│
├── packages/                         shared JS, consumed by apps/web
│   ├── ui/                           ShadCN component library (single source of truth)
│   │   ├── src/
│   │   │   ├── components/           button, dialog, table, form, ...
│   │   │   ├── hooks/
│   │   │   ├── lib/utils.ts          cn() helper
│   │   │   └── styles/globals.css    Tailwind layers + design tokens
│   │   ├── components.json           shadcn CLI config
│   │   ├── package.json
│   │   └── tsconfig.json
│   ├── types/                        shared TS types
│   │   ├── src/
│   │   │   ├── api.generated.ts *    generated from OpenAPI — do not edit
│   │   │   ├── ulpin.ts              identifier shape + parse result types
│   │   │   ├── geo.ts                GeoJSON + 3D Tiles typings
│   │   │   └── domain.ts             parcel, building, storey, VPU
│   │   └── package.json
│   ├── three-core/                   reusable Three.js layer (framework-agnostic)
│   │   ├── src/
│   │   │   ├── scene/                renderer, camera rig, lighting, controls
│   │   │   ├── loaders/              3D Tiles, glTF, Draco, KTX2
│   │   │   ├── picking/              GPU colour-ID picker
│   │   │   ├── clipping/             storey section planes, explode transform
│   │   │   ├── materials/            tenure/status/height shading
│   │   │   └── perf/                 stats, budget guards, culling helpers
│   │   └── package.json
│   ├── config/                       shared tooling config
│   │   ├── eslint/
│   │   ├── tsconfig/
│   │   └── tailwind/
│   └── ulpin-js/                     browser-side ULPIN parse + checksum verify
│       ├── src/
│       └── package.json              MUST mirror backend codec; tested against shared vectors
│
├── database/                         → §4
├── infra/
│   ├── docker/                       → §5
│   ├── k8s/                          (opt) manifests / Helm chart
│   │   ├── base/
│   │   └── overlays/{dev,staging,prod}/
│   ├── terraform/                    (opt) cloud provisioning
│   │   ├── modules/{network,rds,s3,ecs,cdn}/
│   │   └── envs/{dev,staging,prod}/
│   ├── nginx/
│   │   ├── nginx.conf
│   │   └── conf.d/
│   │       ├── api.conf
│   │       ├── web.conf
│   │       └── tiles.conf            long-lived cache headers for tiles/glTF
│   └── monitoring/
│       ├── prometheus/prometheus.yml
│       ├── grafana/dashboards/       api, db, geometry-job, webgl-client
│       ├── loki/loki-config.yml
│       └── alerts/rules.yml
│
├── docs/
│   ├── ARCHITECTURE.md               this set
│   ├── FOLDER-STRUCTURE.md
│   ├── INFRASTRUCTURE.md
│   ├── DEPENDENCIES.md
│   ├── INSTALLATION.md
│   ├── ULPIN-SPEC.md                 normative identifier spec + test vectors
│   ├── DATA-MODEL.md                 ERD + column dictionary
│   ├── API-CONTRACT.md               endpoint surface, error catalogue
│   ├── GEOMETRY-PIPELINE.md          stage-by-stage algorithm notes
│   ├── COORDINATE-SYSTEMS.md         CRS + vertical datum policy
│   ├── SECURITY.md                   threat model, RLS policy matrix
│   ├── DEPLOYMENT.md                 environments, release + rollback
│   ├── RUNBOOK.md                    on-call procedures
│   ├── CONTRIBUTING.md
│   └── adr/                          architecture decision records
│       ├── 0001-monorepo-layout.md
│       ├── 0002-ulpin-encoding-scheme.md
│       ├── 0003-postgis-vs-dedicated-3d-store.md
│       ├── 0004-3d-tiles-over-raw-gltf.md
│       └── 0005-rls-for-jurisdiction-isolation.md
│
├── scripts/
│   ├── bootstrap.sh                  one-shot local setup
│   ├── dev.sh                        compose up + migrate + seed + watch
│   ├── reset-db.sh                   drop, recreate, migrate, seed
│   ├── gen-api-types.sh              OpenAPI → packages/types
│   ├── load-sample-cadastre.sh       demo dataset import
│   ├── make-tiles.sh                 offline tiling for a jurisdiction
│   ├── backup.sh / restore.sh        pg_dump + object-store sync
│   └── smoke-test.sh                 post-deploy check
│
├── tests/
│   ├── e2e/                          Playwright — cross-service journeys
│   │   ├── specs/
│   │   ├── fixtures/
│   │   └── playwright.config.ts
│   ├── load/                         k6 — api + tile throughput
│   └── fixtures/
│       ├── cadastre/                 sample parcels (GeoJSON, Shapefile)
│       ├── buildings/                footprints, floor plates, DXF, IFC
│       └── ulpin-vectors.json        shared codec test vectors (py + ts read this)
│
├── .editorconfig
├── .gitattributes                    LFS for sample geodata + textures
├── .gitignore
├── .dockerignore
├── .nvmrc
├── .python-version
├── .pre-commit-config.yaml
├── docker-compose.yml
├── docker-compose.override.yml       local dev defaults
├── docker-compose.prod.yml
├── Makefile
├── pnpm-workspace.yaml
├── package.json                      root scripts only
├── turbo.json                        (opt) task pipeline + caching
├── LICENSE
└── README.md
```

**Why a monorepo.** The ULPIN codec must exist in Python (authoritative, minting) and
TypeScript (client-side validation before submit). Keeping both in one tree with a shared
`tests/fixtures/ulpin-vectors.json` makes divergence a CI failure rather than a
production defect. See `docs/adr/0001-monorepo-layout.md`.

---

## 2. Backend — `apps/api/`

```
apps/api/
├── app/
│   ├── main.py                       ASGI app factory, lifespan, router mount
│   ├── __init__.py
│   │
│   ├── core/
│   │   ├── config.py                 pydantic-settings, env-typed
│   │   ├── constants.py              enums, tolerances, SRID defaults
│   │   ├── security.py               JWT, password hash, scope checks
│   │   ├── permissions.py            RBAC × jurisdiction matrix
│   │   ├── logging.py                structlog JSON, request/trace ids
│   │   ├── exceptions.py             domain exception hierarchy
│   │   ├── error_handlers.py         → RFC 9457 problem+json
│   │   ├── pagination.py             cursor pagination helpers
│   │   ├── rate_limit.py
│   │   └── telemetry.py              OpenTelemetry init
│   │
│   ├── db/
│   │   ├── base.py                   DeclarativeBase + naming convention
│   │   ├── session.py                async engine, sessionmaker, get_db
│   │   ├── types.py                  custom types (ULPIN, Geometry wrappers)
│   │   ├── listeners.py              audit + updated_at hooks
│   │   └── init_db.py                extension + seed bootstrap
│   │
│   ├── models/                       SQLAlchemy 2.0 + GeoAlchemy2
│   │   ├── mixins.py                 TimestampMixin, SoftDeleteMixin, AuditMixin
│   │   ├── jurisdiction.py
│   │   ├── land_parcel.py
│   │   ├── building.py
│   │   ├── storey.py
│   │   ├── vertical_unit.py          VPU — the 3D ULPIN carrier
│   │   ├── ulpin_record.py           issued identifiers + lineage
│   │   ├── ownership.py
│   │   ├── encumbrance.py
│   │   ├── transaction.py
│   │   ├── generation_job.py
│   │   ├── validation_report.py
│   │   ├── approval.py
│   │   ├── document.py
│   │   ├── tileset.py
│   │   ├── user.py
│   │   ├── api_client.py
│   │   └── audit_event.py
│   │
│   ├── schemas/                      Pydantic v2 — request/response only
│   │   ├── common.py                 Page[T], ProblemDetail, GeoJSON models
│   │   ├── jurisdiction.py
│   │   ├── parcel.py
│   │   ├── building.py
│   │   ├── storey.py
│   │   ├── vertical_unit.py
│   │   ├── ulpin.py                  parse/validate/mint payloads
│   │   ├── ownership.py
│   │   ├── transaction.py
│   │   ├── generation.py             job request, progress event, result
│   │   ├── validation.py
│   │   ├── tiles.py
│   │   ├── auth.py
│   │   └── search.py
│   │
│   ├── api/
│   │   ├── deps.py                   db session, current user, jurisdiction scope
│   │   ├── router.py                 aggregate router
│   │   └── v1/
│   │       ├── __init__.py
│   │       └── endpoints/
│   │           ├── health.py         /healthz /readyz /version
│   │           ├── auth.py           login, refresh, logout, me
│   │           ├── jurisdictions.py
│   │           ├── parcels.py        CRUD + spatial search
│   │           ├── buildings.py
│   │           ├── storeys.py
│   │           ├── units.py          VPU CRUD, 3D query, neighbours
│   │           ├── ulpin.py          decode, verify, resolve, mint, lineage
│   │           ├── generation.py     enqueue, status, SSE stream, cancel
│   │           ├── validation.py     reports, re-run, override with reason
│   │           ├── approvals.py      submit, verify, approve, reject
│   │           ├── ownership.py
│   │           ├── transactions.py
│   │           ├── documents.py      presigned upload, download
│   │           ├── tiles.py          /tiles/{z}/{x}/{y}.mvt, tileset.json
│   │           ├── exports.py        GeoJSON, CityGML, IFC, CSV, PDF certificate
│   │           ├── search.py         unified search (ULPIN, owner, address, bbox)
│   │           ├── stats.py          dashboard aggregates
│   │           └── admin.py          users, roles, policy, reindex
│   │
│   ├── services/                     business logic — the only layer that commits
│   │   ├── parcel_service.py
│   │   ├── building_service.py
│   │   ├── storey_service.py
│   │   ├── unit_service.py
│   │   ├── ulpin_service.py          minting, collision guard, lifecycle transitions
│   │   ├── generation_service.py     pipeline orchestration
│   │   ├── validation_service.py     rule engine execution
│   │   ├── approval_service.py       workflow state machine
│   │   ├── ownership_service.py      share arithmetic, must sum to 1
│   │   ├── transaction_service.py    mutation ledger
│   │   ├── tile_service.py           MVT + tileset assembly, cache keys
│   │   ├── export_service.py
│   │   ├── document_service.py
│   │   ├── search_service.py
│   │   ├── audit_service.py
│   │   └── notification_service.py
│   │
│   ├── repositories/                 queries only — no business rules, no commit
│   │   ├── base.py                   generic async CRUD
│   │   ├── parcel_repo.py
│   │   ├── building_repo.py
│   │   ├── storey_repo.py
│   │   ├── unit_repo.py
│   │   ├── ulpin_repo.py
│   │   ├── spatial_repo.py           raw PostGIS/SFCGAL: intersects3d, volume, MVT
│   │   ├── ownership_repo.py
│   │   ├── job_repo.py
│   │   └── audit_repo.py
│   │
│   ├── ulpin/                        PURE — no DB, no framework imports
│   │   ├── codec.py                  encode / decode / format / normalise
│   │   ├── checksum.py               mod-37,36 check character
│   │   ├── alphabet.py               Crockford Base32, ambiguity folding
│   │   ├── storey_code.py            B/G/M/F/T/A ↔ level integer
│   │   ├── ordinal.py                Hilbert-curve unit ordering
│   │   ├── validators.py             structural + semantic checks
│   │   └── exceptions.py
│   │
│   ├── geo/                          PURE geometry — no DB
│   │   ├── crs.py                    SRID resolution, transform helpers
│   │   ├── datum.py                  ellipsoidal ↔ orthometric height
│   │   ├── cleaning.py               make-valid, snap, sliver removal
│   │   ├── extrusion.py              footprint + heights → solids (LoD1)
│   │   ├── partition.py              storey plate → per-unit prisms
│   │   ├── solids.py                 closure, manifold, volume, boolean ops
│   │   ├── overlap.py                pairwise 3D overlap detection
│   │   ├── reconcile.py              area/volume reconciliation vs sanctioned plan
│   │   ├── tessellate.py             solids → triangle mesh
│   │   ├── gltf.py                   mesh → glTF 2.0 + Draco
│   │   ├── tiles3d.py                glTF → 3D Tiles 1.1 tileset
│   │   ├── mvt.py                    vector tile assembly
│   │   └── simplify.py               LoD reduction
│   │
│   ├── ingest/
│   │   ├── readers/
│   │   │   ├── geojson_reader.py
│   │   │   ├── shapefile_reader.py
│   │   │   ├── dxf_reader.py
│   │   │   ├── ifc_reader.py         (opt) BIM import
│   │   │   ├── csv_reader.py         unit schedules / floor tables
│   │   │   └── pointcloud_reader.py  (opt) LAS/LAZ
│   │   ├── normalizers/              CRS, units, attribute mapping
│   │   ├── matchers/                 footprint ↔ parcel association
│   │   └── staging.py                staging-table load + promotion
│   │
│   ├── workers/
│   │   ├── celery_app.py             broker, queues, routing
│   │   ├── beat.py                   scheduled tasks
│   │   └── tasks/
│   │       ├── ingest_tasks.py
│   │       ├── generation_tasks.py   extrude, partition, mint
│   │       ├── validation_tasks.py
│   │       ├── tiling_tasks.py
│   │       ├── export_tasks.py
│   │       ├── notification_tasks.py
│   │       └── maintenance_tasks.py  vacuum, reindex, tile GC, audit archive
│   │
│   ├── integrations/
│   │   ├── storage/                  S3/MinIO client, presigned URLs
│   │   ├── auth_provider/            OIDC/Keycloak client
│   │   ├── land_registry/            external registry adapters
│   │   ├── municipal/                property-tax / utility connectors
│   │   └── email/
│   │
│   ├── middleware/
│   │   ├── request_id.py
│   │   ├── timing.py
│   │   ├── auth.py
│   │   ├── tenant.py                 sets RLS GUC per request
│   │   ├── compression.py
│   │   └── security_headers.py
│   │
│   └── templates/                    PDF/report templates (Jinja2)
│       ├── certificate_3d_ulpin.html
│       ├── validation_report.html
│       └── partials/
│
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/                     → mirrors database/migrations, see §4
├── tests/
│   ├── conftest.py                   testcontainers PostGIS fixture
│   ├── unit/
│   │   ├── ulpin/                    codec, checksum, ordinal — exhaustive
│   │   ├── geo/                      extrusion, partition, solids
│   │   └── services/
│   ├── integration/
│   │   ├── api/                      per-endpoint
│   │   ├── db/                       RLS, triggers, constraints
│   │   └── workers/
│   ├── property/                     hypothesis — codec round-trip, partition invariants
│   ├── fixtures/
│   └── factories/                    factory-boy model factories
│
├── alembic.ini
├── pyproject.toml                    deps, ruff, mypy, pytest config
├── uv.lock  /  requirements*.txt
├── Dockerfile
├── Dockerfile.dev
├── .env.example
├── .env +                            local only, gitignored
└── README.md
```

### 2.1 Backend conventions

- **Async everywhere** — `asyncpg` + SQLAlchemy async session. Geometry-heavy CPU work
  goes to Celery, never inline in a request.
- **No ORM objects cross the API boundary.** Endpoints return Pydantic schemas.
- **Transactions open in `services/`, never in `repositories/` or endpoints.**
- **Errors** are `problem+json` (RFC 9457) with a stable `type` URI per error code, so
  clients can branch on machine-readable values.
- **Every raw SQL string lives in `repositories/`** — nowhere else. PostGIS/SFCGAL calls
  are concentrated in `spatial_repo.py`.

---

## 3. Frontend — `apps/web/`

Next.js 15 App Router, React Server Components by default, Tailwind v4, ShadCN from
`packages/ui`.

```
apps/web/
├── src/
│   ├── app/
│   │   ├── layout.tsx                root shell, providers, fonts
│   │   ├── globals.css               Tailwind layers + tokens
│   │   ├── error.tsx / not-found.tsx / loading.tsx
│   │   │
│   │   ├── (public)/
│   │   │   ├── page.tsx              landing
│   │   │   ├── search/page.tsx       public ULPIN lookup
│   │   │   ├── ulpin/[ulpin]/page.tsx  public unit record
│   │   │   └── verify/page.tsx       certificate verification
│   │   │
│   │   ├── (auth)/
│   │   │   ├── login/page.tsx
│   │   │   ├── callback/page.tsx     OIDC redirect handler
│   │   │   └── layout.tsx
│   │   │
│   │   ├── (dashboard)/
│   │   │   ├── layout.tsx            sidebar + topbar + breadcrumbs
│   │   │   ├── dashboard/page.tsx    KPIs, recent jobs, approvals queue
│   │   │   │
│   │   │   ├── parcels/
│   │   │   │   ├── page.tsx          table + filters + map toggle
│   │   │   │   ├── [id]/
│   │   │   │   │   ├── page.tsx      parcel detail
│   │   │   │   │   ├── buildings/page.tsx
│   │   │   │   │   └── documents/page.tsx
│   │   │   │   └── new/page.tsx
│   │   │   │
│   │   │   ├── buildings/
│   │   │   │   └── [id]/
│   │   │   │       ├── page.tsx      overview + LoD1 preview
│   │   │   │       ├── storeys/page.tsx
│   │   │   │       ├── units/page.tsx
│   │   │   │       ├── model/page.tsx        full 3D workspace
│   │   │   │       └── validation/page.tsx
│   │   │   │
│   │   │   ├── units/
│   │   │   │   └── [ulpin]/
│   │   │   │       ├── page.tsx      VPU record + 3D locator
│   │   │   │       ├── ownership/page.tsx
│   │   │   │       ├── transactions/page.tsx
│   │   │   │       └── lineage/page.tsx      supersession graph
│   │   │   │
│   │   │   ├── generation/
│   │   │   │   ├── page.tsx          job list
│   │   │   │   ├── new/page.tsx      wizard: source → params → preview → run
│   │   │   │   └── [jobId]/page.tsx  live progress (SSE) + stage log
│   │   │   │
│   │   │   ├── map/page.tsx          full-screen 2D + 3D linked explorer
│   │   │   ├── approvals/            queue, [id] review screen
│   │   │   ├── imports/              upload, mapping, staging preview
│   │   │   ├── reports/              exports, certificates
│   │   │   └── admin/                users, roles, jurisdictions, audit, settings
│   │   │
│   │   └── api/                      route handlers (BFF only)
│   │       ├── auth/[...nextauth]/route.ts
│   │       ├── proxy/[...path]/route.ts      token-attaching API proxy
│   │       └── og/route.tsx                  (opt) social cards
│   │
│   ├── components/
│   │   ├── ui/                       re-export from packages/ui
│   │   ├── layout/                   sidebar, topbar, breadcrumbs, jurisdiction switcher
│   │   ├── parcels/                  table, filters, form, summary card
│   │   ├── buildings/                storey list, height editor, footprint editor
│   │   ├── units/                    unit table, tenure badge, ULPIN chip w/ copy
│   │   ├── ulpin/
│   │   │   ├── ulpin-input.tsx       masked input + live checksum feedback
│   │   │   ├── ulpin-breakdown.tsx   visual decomposition of the 30 chars
│   │   │   └── ulpin-badge.tsx       status-coloured pill
│   │   ├── map2d/                    MapLibre wrapper, layers, legend, draw tools
│   │   ├── viewer3d/                 React bindings over packages/three-core
│   │   │   ├── scene-canvas.tsx      the single <canvas> host
│   │   │   ├── building-model.tsx
│   │   │   ├── storey-slider.tsx     vertical section control
│   │   │   ├── explode-control.tsx
│   │   │   ├── unit-inspector.tsx    click-to-inspect panel
│   │   │   ├── measure-tool.tsx
│   │   │   ├── legend-3d.tsx
│   │   │   └── view-presets.tsx      plan / elevation / iso
│   │   ├── generation/               wizard steps, progress, stage log
│   │   ├── validation/               issue list, severity chips, fix hints
│   │   ├── approvals/                diff view, decision form
│   │   ├── forms/                    RHF + zod field primitives
│   │   ├── data-table/               TanStack table shell, column defs, toolbar
│   │   ├── charts/                   dashboard visualisations
│   │   └── shared/                   empty state, error boundary, confirm dialog
│   │
│   ├── lib/
│   │   ├── api/
│   │   │   ├── client.ts             fetch wrapper, auth, problem+json parsing
│   │   │   ├── endpoints/            one module per resource
│   │   │   └── query-keys.ts         TanStack Query key factory
│   │   ├── auth/                     session helpers, server-side guard
│   │   ├── geo/                      bbox, CRS display, GeoJSON helpers
│   │   ├── ulpin/                    re-export packages/ulpin-js + format helpers
│   │   ├── utils/                    cn, dates, numbers, units (sq ft ↔ m²)
│   │   └── validators/               zod schemas mirroring backend
│   │
│   ├── hooks/
│   │   ├── use-parcels.ts / use-units.ts / use-generation-job.ts
│   │   ├── use-sse.ts                job progress stream
│   │   ├── use-map-sync.ts           keeps 2D and 3D cameras locked
│   │   ├── use-selection.ts          cross-view selection state
│   │   └── use-permissions.ts
│   │
│   ├── stores/                       Zustand — client-only UI state
│   │   ├── viewer-store.ts           camera, section plane, explode factor
│   │   ├── selection-store.ts
│   │   ├── filter-store.ts
│   │   └── ui-store.ts
│   │
│   ├── providers/                    query, theme, session, scene, toast
│   ├── types/                        local types; shared ones live in packages/types
│   ├── config/
│   │   ├── site.ts / nav.ts / map.ts / viewer.ts   (LoD thresholds, budgets)
│   │   └── permissions.ts
│   ├── styles/                       theme.css, map.css, print.css
│   └── middleware.ts                 auth gate, locale, CSP nonce
│
├── public/
│   ├── draco/                        Draco decoder (must be self-hosted)
│   ├── basis/                        KTX2 transcoder
│   ├── models/                       static demo assets
│   ├── icons/ · fonts/ · images/
│   └── favicon.ico · manifest.webmanifest
│
├── tests/
│   ├── unit/                         vitest + testing-library
│   ├── integration/
│   └── __mocks__/                    MSW handlers
│
├── components.json                   shadcn CLI
├── next.config.ts
├── tailwind.config.ts                (Tailwind v4: mostly CSS-first, config is thin)
├── postcss.config.mjs
├── tsconfig.json
├── vitest.config.ts
├── eslint.config.mjs
├── package.json
├── Dockerfile
├── .env.local.example
├── .env.local +
└── README.md
```

### 3.1 Frontend conventions

- **RSC by default.** `"use client"` only where interaction demands it — the 3D viewer,
  forms, and anything touching Zustand. Data fetching for first paint happens on the
  server; TanStack Query handles client-side mutation and refetch.
- **One WebGL context per document.** `SceneCanvas` is mounted once in the dashboard
  layout and portalled where needed. Multiple canvases exhaust context limits and tank
  performance — this is the rule people break first.
- **`packages/three-core` stays React-free** so the render loop can be tested headlessly
  and reused outside Next.js.
- **Never re-render the scene from React state.** React sets store values; the render
  loop reads them via `subscribe`. Camera movement must not trigger React reconciliation.
- **Server Actions** for mutations that don't need optimistic UI; the API proxy route
  handler for everything else.

---

## 4. Database — `database/`

```
database/
├── migrations/                       Alembic revisions (symlinked into apps/api/alembic)
│   └── versions/
│       ├── 0001_enable_extensions.py       postgis, postgis_sfcgal, pgcrypto,
│       │                                   uuid-ossp, pg_trgm, btree_gist
│       ├── 0002_core_enums_and_domains.py  ULPIN domain w/ CHECK, status enums
│       ├── 0003_jurisdictions.py
│       ├── 0004_users_roles_clients.py
│       ├── 0005_land_parcels.py
│       ├── 0006_buildings.py
│       ├── 0007_storeys.py
│       ├── 0008_vertical_units.py
│       ├── 0009_ulpin_records_and_lineage.py
│       ├── 0010_ownership_encumbrance.py
│       ├── 0011_transactions.py
│       ├── 0012_generation_jobs.py
│       ├── 0013_validation_reports.py
│       ├── 0014_approvals.py
│       ├── 0015_documents_tilesets.py
│       ├── 0016_audit_events.py
│       ├── 0017_spatial_indexes.py         GiST + ND-GiST + partial indexes
│       ├── 0018_rls_policies.py
│       ├── 0019_triggers_immutability.py
│       ├── 0020_materialized_views.py
│       └── 0021_partitioning_audit.py      monthly range partitions
│
├── schemas/                          reference DDL — documentation, not executed
│   ├── 00_extensions.sql
│   ├── 01_reference.sql              jurisdictions, land-use codes, tenure types
│   ├── 02_cadastre.sql               parcels, buildings, storeys
│   ├── 03_vertical.sql               VPUs, solids, ULPIN records, lineage
│   ├── 04_registry.sql               ownership, encumbrance, transactions
│   ├── 05_workflow.sql               jobs, validation, approvals
│   ├── 06_security.sql               users, roles, RLS policies
│   ├── 07_audit.sql
│   └── 08_views.sql
│
├── functions/                        PL/pgSQL — deterministic, tested via pgTAP
│   ├── ulpin_checksum.sql            MUST match app/ulpin/checksum.py exactly
│   ├── ulpin_validate.sql
│   ├── ulpin_parse.sql               → (parent, block, storey, unit) record
│   ├── generate_storey_code.sql
│   ├── vpu_volume.sql                SFCGAL volume with CRS transform
│   ├── vpu_overlap_check.sql
│   ├── parcel_area_metric.sql
│   ├── hilbert_index.sql             unit ordinal support
│   ├── mvt_parcels.sql / mvt_units.sql
│   └── audit_trigger.sql
│
├── views/
│   ├── v_parcel_summary.sql
│   ├── v_building_summary.sql
│   ├── v_unit_full.sql               VPU + ownership + status, denormalised
│   ├── v_ulpin_lookup.sql            single-hit resolution for public search
│   ├── mv_jurisdiction_stats.sql     materialized, refreshed nightly
│   └── mv_tile_units.sql             materialized, tile-optimised geometry
│
├── seeds/
│   ├── 01_jurisdictions.sql          state → district → taluk → village
│   ├── 02_reference_codes.sql        land use, tenure, unit types, storey types
│   ├── 03_roles_permissions.sql
│   ├── 04_admin_user.sql             dev only
│   └── demo/
│       ├── parcels.geojson
│       ├── buildings.geojson
│       ├── floor_plates.geojson
│       └── unit_schedule.csv
│
├── policies/
│   ├── rls_jurisdiction.sql
│   ├── rls_role_matrix.sql
│   └── column_privileges.sql         PII restriction per role
│
├── tests/                            pgTAP
│   ├── test_ulpin_functions.sql
│   ├── test_constraints.sql
│   ├── test_rls.sql
│   ├── test_triggers.sql
│   └── test_spatial_functions.sql
│
├── maintenance/
│   ├── vacuum_analyze.sql
│   ├── reindex_spatial.sql
│   ├── refresh_matviews.sql
│   ├── archive_audit.sql
│   └── cluster_geometry.sql          CLUSTER on GiST for locality
│
├── backup/
│   ├── pg_backup.sh
│   ├── pg_restore.sh
│   └── wal_archive.conf
│
├── config/
│   ├── postgresql.custom.conf        work_mem, maintenance_work_mem, JIT off for GIS
│   ├── pg_hba.custom.conf
│   └── tuning-notes.md
│
├── docker-entrypoint-initdb.d/       runs once on first container start
│   ├── 01-init-extensions.sh
│   ├── 02-create-roles.sh
│   └── 03-init-db.sh
│
└── README.md
```

### 4.1 Database conventions

- **Alembic owns the schema.** Files in `schemas/` are generated documentation; the
  migration chain is the only thing that runs. CI asserts `upgrade head` →
  `downgrade base` → `upgrade head` on a clean PostGIS container.
- **The ULPIN checksum exists in three languages** (Python, TypeScript, PL/pgSQL). All
  three read `tests/fixtures/ulpin-vectors.json`. A mismatch fails CI.
- **A `ulpin` DOMAIN with a CHECK constraint** keeps malformed identifiers out at the
  storage layer, independent of application correctness.
- **Audit is append-only** — `REVOKE UPDATE, DELETE` on `audit_events` for all app roles,
  monthly range partitions, archived after 7 years.
- **Issued VPU geometry is trigger-protected.** Once `status = 'ISSUED'`, updates to
  `volume_solid` and `ulpin` raise an exception; corrections go through supersession.
- **Two app roles**: `ulpin_app` (DML, RLS-bound) and `ulpin_migrate` (DDL, used only by
  Alembic). The API never connects with DDL rights.
