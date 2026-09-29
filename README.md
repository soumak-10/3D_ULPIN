<div align="center">

<img src="apps/web/public/logo.svg" width="72" alt="">

# 3D ULPIN Generation and Vertical Property Mapping System

**India's 14-character land-parcel identifier, extended into the third dimension.**

**🛑FIXING SOME BUGS🐛🛑**

Next.js 15 · FastAPI · PostgreSQL + PostGIS · Three.js

</div>

---

## The problem

India's DILRMP programme gives every land parcel a 14-character ULPIN — the
"Bhu-Aadhaar". It identifies ground. It has nothing to say about the forty-two
separately-owned flats stacked on top of that ground.

A parcel identifier cannot distinguish flat 301 from flat 302. So the register
describes the plot precisely and the actual property — the volume someone owns,
lives in, mortgages and eventually sells — only in prose. Disputes over
vertical property therefore turn on documents rather than on the register.

This system issues a derived identifier for every owned volume:

```
WB-KOL-B001-F03-U301
│  │   │    │   └── unit 301
│  │   │    └────── floor 3
│  │   └─────────── building 001
│  └─────────────── Kolkata
└────────────────── West Bengal
```

Each identifier carries a PostGIS **solid** — a closed 3D volume with a real
`ST_Volume`, validated by `ST_IsSolid`. Two flats cannot occupy the same cubic
metre, and the database can prove it.

Every unit keeps both forms: the short code above, quotable over a counter, and
the parcel-derived 14-character code that DILRMP and every downstream system
key on. Neither is derivable from the other, so both are stored and both are
uniquely indexed.

---

## What it does

| Module | |
|---|---|
| **Authentication** | JWT access tokens in memory, opaque refresh tokens in an `HttpOnly` cookie, Argon2id hashing, four roles |
| **Property registration** | Buildings → floors → units, with owners, tenants and occupancy |
| **ULPIN generation** | Automatic, collision-free, sequence-guarded at the database level |
| **3D viewer** | Three.js. One clickable mesh per unit; green verified, yellow pending, red flagged |
| **Search** | Trigram search across ULPIN, owner, tenant and building, with filters and pagination |
| **Verification** | Five checks — record, geometry, ownership, tenancy, occupancy — and one verdict |
| **Fraud detection** | Five rules, three severities, fingerprinted alerts, officer decisions |
| **Dashboard** | Seven statistics, four charts, three activity tables |

---

## Quick start

```bash
git clone <repository-url> ulpin-3d-system
cd ulpin-3d-system
make setup
```

Put a `SECRET_KEY` in `.env` — compose refuses to start without one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Then:

```bash
make dev
```

<http://localhost:3000> · API docs at <http://localhost:8000/docs>

Seeded accounts and their caveat — the committed password hashes are
placeholders — are in [INSTALLATION.md §8.2](docs/INSTALLATION.md#82-first-login).

---

## Layout

```
ulpin-3d-system/
├── apps/
│   ├── api/                 FastAPI — 56 endpoints
│   │   └── app/
│   │       ├── api/         routers
│   │       ├── core/        config, security, dependencies
│   │       ├── models/      SQLAlchemy 2.0
│   │       ├── repositories/ queries
│   │       ├── schemas/     pydantic
│   │       ├── services/    verification, fraud, search
│   │       └── ulpin/       generation and validation
│   └── web/                 Next.js 15 App Router
│       └── src/
│           ├── app/(app)/   dashboard, buildings, units, ulpins,
│           │                owners, viewer, search, verification, fraud
│           ├── app/(auth)/  login, register, forgot/reset password
│           ├── components/  ui (ShadCN), shared, viewer, charts
│           ├── lib/         api client, validators, constants
│           └── providers/   auth, query, theme
├── database/
│   ├── schemas/             00 extensions → 05 modules, run in order
│   ├── seeds/               demonstration data, including planted fraud
│   └── docs/                ER diagram
├── infra/                   compose, Dockerfiles, nginx, db bootstrap
└── docs/                    architecture, infrastructure, dependencies, installation
```

---

## Documentation

| | |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, data flow, module boundaries |
| [FOLDER-STRUCTURE.md](docs/FOLDER-STRUCTURE.md) | Every directory, and what belongs in it |
| [INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) | Docker configuration, environment variables |
| [DEPENDENCIES.md](docs/DEPENDENCIES.md) | Every package, and why that one |
| [INSTALLATION.md](docs/INSTALLATION.md) | Setup, verification, troubleshooting, production |
| [ER-DIAGRAM.md](database/docs/ER-DIAGRAM.md) | The ten tables and their relationships |

---

## Four decisions worth knowing

**The browser never calls FastAPI directly.** Next rewrites `/api/v1` to the
API server-side. That indirection is what lets the refresh cookie stay
`SameSite=Lax` — a cross-origin API would force `SameSite=None`, and a cookie
that travels on third-party requests is what `SameSite` exists to prevent.

**Missing data is not evidence of fraud.** A verification check that fails
because the register is incomplete yields *Pending Verification*. Only
contradictory data — two primary owners, an occupant with no tenancy — produces
an adverse verdict. The two are rendered differently on purpose: "inconclusive"
and "contradicted" are not the same finding, and an officer acting on one
should never think they are acting on the other.

**An alert is a finding, not a determination.** Fraud alerts are fingerprinted
`sha256(rule:subject)`, so re-running a scan updates the existing alert instead
of raising a second copy. Every alert carries the raw evidence the rule
compared, because an officer defending a decision needs the values, not a
paraphrase of them.

**The application cannot rewrite its own audit trail.** `ulpin_app` holds
`INSERT` and `SELECT` on `audit_logs` and nothing else; `UPDATE`, `DELETE` and
`TRUNCATE` are revoked in the schema. Pointing the API at the superuser to
clear a permission error silently discards this.

---

## Development

```bash
make help          # every target
make logs s=api    # follow one service
make db-shell      # psql
make check         # lint, tests, production build — what CI runs
make db-reset      # drop the volume and re-bootstrap (destructive)
```

Running on the host instead of in containers, and the manual database setup,
are in [INSTALLATION.md §8.4](docs/INSTALLATION.md#84-manual-installation).

---

## Status

All eight modules are implemented end to end. The backend exposes 56 endpoints
against ten tables with PostGIS 3D geometry; the frontend covers every module
with routing, shared error and loading states, and notifications.

Known gaps, stated rather than hidden:

- Seeded password hashes are placeholders — see [§8.2](docs/INSTALLATION.md#82-first-login)
- `npm install` has not been run, so there is no committed `package-lock.json`; the web Dockerfile falls back to `npm install`
- Test coverage is a smoke suite over routing and the ULPIN grammar, not the verification and fraud services
- Alembic owns migrations from the initial raw-SQL schema onward; there are no migrations yet because the schema has not changed since

---

## Licence

Apache-2.0. See [LICENSE](LICENSE).
