# Dependencies

**§7 package dependencies**

Companion to [ARCHITECTURE.md](ARCHITECTURE.md). Every direct dependency, and
why it is there rather than something else.

Manifests: [`apps/api/pyproject.toml`](../apps/api/pyproject.toml) ·
[`apps/web/package.json`](../apps/web/package.json)

---

## 7.1 Backend — Python 3.12+

Pins are lower bounds with an upper guard on majors that have broken things
before. Exact versions belong in a lockfile; the manifest states intent.

### Web

| Package | Pin | Why |
|---|---|---|
| `fastapi` | `>=0.115,<0.120` | Type-driven routing, dependency injection, OpenAPI for free. The upper bound is real: FastAPI still makes breaking changes in minor releases. |
| `uvicorn[standard]` | `>=0.32,<0.40` | ASGI server. `[standard]` brings `uvloop` and `httptools`. |
| `python-multipart` | `>=0.0.18` | Form parsing for deed-scan uploads. Below `0.0.18` is CVE-2024-53981. |

### Validation and settings

| Package | Pin | Why |
|---|---|---|
| `pydantic` | `>=2.9,<3` | v2's Rust core. Request schemas are validated once, at the boundary. |
| `pydantic-settings` | `>=2.6,<3` | Environment parsing with the same validators — configuration fails at import, not at first use. |
| `email-validator` | `>=2.2` | Backs `EmailStr`. Pydantic does not pull it in. |

### Database

| Package | Pin | Why |
|---|---|---|
| `SQLAlchemy[asyncio]` | `>=2.0.36,<2.1` | 2.0 typed ORM. `<2.1` because `Mapped[...]` inference is version-sensitive. |
| `asyncpg` | `>=0.30` | Async driver, used by the application. |
| `psycopg[binary]` | `>=3.2` | Sync driver. Alembic runs migrations synchronously; both drivers coexist, and `config.py` exposes a URI for each. |
| `GeoAlchemy2` | `>=0.15` | PostGIS column types (`Geometry`, `Geography`) and the WKB round-trip. Without it, geometry columns are opaque bytes to the ORM. |
| `alembic` | `>=1.14` | Migrations. The initial schema is raw SQL in `database/schemas/`; Alembic owns everything after it. |

### Security

| Package | Pin | Why |
|---|---|---|
| `argon2-cffi` | `>=23.1` | **Argon2id**, the password hash. Memory-hard, so GPU attacks cost RAM rather than cores. Parameters are configurable per deployment (`ARGON2_*`). |
| `PyJWT[crypto]` | `>=2.9,<3` | Access tokens. Chosen over `python-jose`, which has had unpatched advisories and a slower release cadence. `[crypto]` allows a later move to RS256 without a dependency change. |

### Runtime

| Package | Pin | Why |
|---|---|---|
| `anyio` | `>=4.6` | Structured concurrency; already FastAPI's async substrate. |
| `redis` | `>=5.2` | Rate-limit counters. Without it the limit is per-process, so *N* replicas permit *N*× the configured rate. |
| `httpx` | `>=0.28` | Outbound calls and `TestClient`. |

### Development extra

`pytest`, `pytest-asyncio`, `pytest-cov`, `ruff` (lint **and** format — replaces
black + isort + flake8), `mypy`, `types-redis`, `faker`.

```bash
pip install -e ".[dev]"
```

### Deliberately absent

- **`celery`** — no background work yet. Fraud scans are synchronous and bounded; adding a broker before there is a queue is infrastructure without a user.
- **`python-jose`** — see `PyJWT` above.
- **`passlib`** — unmaintained since 2020 and its bcrypt backend warns on modern bcrypt. `argon2-cffi` is used directly.
- **`shapely`/`geopandas`** — geometry work belongs in PostGIS, next to the data and the spatial index. Pulling rows into Python to compute a volume is slower and gives a second, disagreeing answer.

---

## 7.2 Frontend — Node 20+ (22 recommended)

### Framework

| Package | Version | Why |
|---|---|---|
| `next` | `15.1.6` | App Router, server components, the `/api/v1` rewrite that keeps the refresh cookie `SameSite=Lax`. |
| `react`, `react-dom` | `19.0.0` | Required by Next 15. |

### Forms and validation

| Package | Version | Why |
|---|---|---|
| `react-hook-form` | `^7.54.2` | Uncontrolled inputs — a 30-field registration form does not re-render the page on every keystroke. |
| `zod` | `^3.24.1` | One schema per form in `lib/validators.ts`, reused for the TypeScript type via `z.infer`. |
| `@hookform/resolvers` | `^3.10.0` | Bridges the two. |

`zod` stays on 3.x: `zodResolver` in resolvers 3.x targets the 3.x API.

### Server state

| Package | Version | Why |
|---|---|---|
| `@tanstack/react-query` | `^5.64.2` | Caching, deduplication, invalidation. `staleTime: 60s`, `refetchOnWindowFocus: false`, retries refused on 4xx, and `placeholderData: (prev) => prev` so paging a table does not blank it. |

No Redux, no Zustand. Nearly all state here is server state; the little that is
not (access token, theme) lives in two small contexts. Adding a client store
would mean caching the same rows twice and choosing which copy is true.

### 3D

| Package | Version | Why |
|---|---|---|
| `three` | `^0.172.0` | The viewer. One mesh per unit — a merged floor is cheaper to draw and impossible to click. |
| `@types/three` | `^0.172.0` | Must track `three` exactly; the types ship separately and drift. |
| `@react-three/fiber` | `^9.0.0` | Installed for future declarative scenes. |
| `@react-three/drei` | `^10.0.0` | Helpers, likewise. |

`components/viewer/building-scene.tsx` uses **raw Three.js**, not R3F. Two
imperative concerns — a raycast on click and an animation loop — and hover
state must not round-trip through React on every mouse move. Colour changes
mutate materials in place rather than rebuilding the scene.

### UI

| Package | Version | Why |
|---|---|---|
| `@radix-ui/react-*` | 8 primitives | ShadCN's foundation: dialog, dropdown-menu, label, select, separator, slot, tabs, tooltip. Unstyled, accessible, keyboard-complete. |
| `tailwindcss` | `^3.4.17` | Utility CSS. Semantic colour families `verified`/`pending`/`fraud` are defined once as CSS variables. |
| `class-variance-authority` | `^0.7.1` | Typed component variants. |
| `clsx` + `tailwind-merge` | — | `cn()`: conditional classes with later utilities winning. |
| `tailwindcss-animate` | `^1.0.7` | Radix enter/exit animations. |
| `lucide-react` | `^0.474.0` | Icons. Tree-shaken per import. |
| `next-themes` | `^0.4.4` | Dark mode without a flash on first paint. |
| `sonner` | `^1.7.2` | Toasts. |
| `recharts` | `^2.15.0` | The four dashboard charts. SVG, so they print — which matters for a government dashboard. |

ShadCN itself is not a dependency: its components are copied into
`components/ui/` and owned outright.

`three` and `recharts` are the two heavy packages. Both are loaded only on the
routes that need them — the viewer via `next/dynamic` with `ssr: false`.

### Colour is centralised

`statusTone()` decides green/yellow/red once. Tailwind reads the CSS
variables; Three.js cannot, so `TONE_HEX` mirrors them for materials. An open
fraud alert overrides the verification outcome — a unit cannot be green in the
search results and red in the model.

---

## 7.3 System requirements

| | Minimum | Recommended |
|---|---|---|
| Python | 3.12 | 3.12 |
| Node | 20 | 22 LTS |
| PostgreSQL | 14 | 16 |
| PostGIS | 3.2 | 3.4 **with SFCGAL** |
| Docker Engine | 24 | 27 |
| RAM | 4 GB | 8 GB |

PostGIS **with SFCGAL** is not optional: `ST_Volume` and `ST_IsSolid` are what
make a unit a property rather than a label. `postgis/postgis:16-3.4` includes
it; `postgres:16` does not.

WebGL 2 is required in the browser for the viewer. Every other page works
without it.

---

## 7.4 Auditing

```bash
cd apps/api && pip install pip-audit && pip-audit
cd apps/web && npm audit --omit=dev
```

Transitive advisories in build-time-only packages are noise; advisories in
anything that reaches production are not.

---

Next: [INSTALLATION.md](INSTALLATION.md)
