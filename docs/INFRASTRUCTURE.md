# Infrastructure

**§5 Docker configuration · §6 environment variables**

Companion to [ARCHITECTURE.md](ARCHITECTURE.md). Where that document says what
the system is, this one says what it runs on.

---

## 5. Docker configuration

### 5.1 Topology

```
                         ┌──────────────┐
   browser ─── :3000 ───▶│  web         │  Next.js 15
                         │  (Node 22)   │
                         └──────┬───────┘
                                │  /api/v1/* rewrite, server-side
                         ┌──────▼───────┐
                         │  api         │  FastAPI / uvicorn
                         │  (Python 3.12)│
                         └───┬──────┬───┘
                             │      │
              ┌──────────────▼┐   ┌─▼──────────────┐
              │  postgres     │   │  redis         │
              │  PostGIS 3.4  │   │  rate limits   │
              │  + SFCGAL     │   │                │
              └───────────────┘   └────────────────┘
```

Four services. In production an `nginx` service sits in front of `web` and
`api` and terminates TLS ([`infra/nginx/conf.d/ulpin.conf`](../infra/nginx/conf.d/ulpin.conf));
in development Next's own rewrite does the same routing, so the two topologies
are identical from the browser's point of view.

**Why the browser never talks to FastAPI directly.** The refresh token lives in
an `HttpOnly` cookie. A cross-origin API would force `SameSite=None` on that
cookie, and a cookie that travels on third-party requests is precisely what
`SameSite` exists to prevent. Routing `/api/v1` through the frontend's own
origin keeps it `SameSite=Lax`. This is the single constraint that shapes the
whole deployment, and it is why `API_ORIGIN` has no `NEXT_PUBLIC_` prefix.

### 5.2 Files

| Path | Purpose |
|---|---|
| [`infra/docker-compose.yml`](../infra/docker-compose.yml) | Development stack: postgres, redis, api, web |
| [`infra/docker/api.Dockerfile`](../infra/docker/api.Dockerfile) | Multi-stage: `base` → `deps` → `development` \| `production` |
| [`infra/docker/web.Dockerfile`](../infra/docker/web.Dockerfile) | Multi-stage: `base` → `deps` → `development` \| `builder` → `production` |
| [`infra/postgres/initdb/00-bootstrap.sh`](../infra/postgres/initdb/00-bootstrap.sh) | Runs the schema in order, then the seeds |
| [`infra/nginx/conf.d/ulpin.conf`](../infra/nginx/conf.d/ulpin.conf) | TLS termination, routing, rate limiting |
| [`infra/nginx/conf.d/proxy-params.conf`](../infra/nginx/conf.d/proxy-params.conf) | Shared proxy headers |
| [`Makefile`](../Makefile) | `make help` for the full list |

### 5.3 The database image is not interchangeable

`postgis/postgis:16-3.4`, not `postgres:16`. The vertical model needs SFCGAL —
`ST_Volume`, `ST_IsSolid`, `ST_3DIntersection`, `ST_Extrude` — and none of it
exists in the stock image. A unit's volume is what makes it a *property* rather
than a label on a floor plan, so this is a functional dependency, not a
convenience.

`POSTGRES_INITDB_ARGS` pins the collation to `C`. Left to the host locale, two
machines sort owner names differently, and the trigram search ranks results
differently on each of them — a class of bug that only ever reproduces on
someone else's laptop.

### 5.4 Initialisation order

The Postgres entrypoint runs files in `/docker-entrypoint-initdb.d`
alphabetically, and only the files directly inside it — subdirectories are
ignored. The schema is mounted read-only at `/sql` and driven explicitly by
`00-bootstrap.sh`:

```
00_extensions.sql  → PostGIS, SFCGAL, pg_trgm, pgcrypto; roles; schema
01_types.sql       → 13 enum types
02_tables.sql      → 10 core tables
03_indexes.sql     → B-tree, GiST (spatial), GIN (trigram)
04_triggers.sql    → audit, updated_at, ULPIN sequence guards
05_modules.sql     → verification and fraud support objects
01_seed.sql        → demonstration data (SEED_DATABASE=true)
```

The numbering is load-bearing: types before tables, tables before indexes,
indexes before triggers. Globbing two mounted directories would interleave the
seeds into the middle of the schema.

### 5.5 Three database roles

| Role | Used by | Can |
|---|---|---|
| `postgres` | bootstrap only | everything, including `CREATE EXTENSION` |
| `ulpin_app` | the API | `SELECT/INSERT/UPDATE/DELETE`; **`INSERT`/`SELECT` only on `audit_logs`** |
| `ulpin_readonly` | reporting, BI | `SELECT` |

`REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM ulpin_app` is enforced in
`04_triggers.sql`. An audit trail the application can rewrite is not an audit
trail. Pointing the API at the superuser to "fix a permission error" silently
removes this property — fix the grant instead.

### 5.6 Healthchecks

`pg_isready` returns true while init scripts are still running, so the postgres
probe queries `ulpin.units` instead: it passes only once the schema is actually
loaded. `api` depends on `service_healthy`, so a first `up --build` on an empty
volume waits for the full bootstrap rather than crash-looping against a
half-built database.

### 5.7 Development versus production targets

|  | development | production |
|---|---|---|
| API source | bind-mounted, `--reload` | copied in, 4 workers |
| Web source | bind-mounted, `next dev` | built, `next start` |
| User | root | uid 10001, non-root |
| Type errors | surface at runtime | **fail the image build** |

`next.config.ts` sets `ignoreBuildErrors: false` and `ignoreDuringBuilds:
false`, so `npm run build` inside the web image type-checks and lints. A broken
type does not reach a registry.

Switch both with one line in `.env`:

```bash
API_TARGET=production WEB_TARGET=production NODE_ENV=production
```

### 5.8 Backup

The data is the deliverable; everything else is rebuildable.

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  pg_dump -U postgres -Fc ulpin_db > ulpin-$(date +%F).dump
```

`-Fc` (custom format) keeps PostGIS geometry intact and restores selectively.
`ID_HASH_PEPPER` belongs in the same backup: rotate it and every stored
`national_id_hash` becomes unmatchable, which is indistinguishable from losing
the column.

---

## 6. Environment variables

Three files, three scopes. Each has a committed `.example`; none of the real
ones are tracked.

| File | Scope | Read by |
|---|---|---|
| [`.env`](../.env.example) | containers | docker compose |
| [`apps/api/.env`](../apps/api/.env.example) | backend | `app/core/config.py` |
| [`apps/web/.env.local`](../apps/web/.env.example) | frontend | `next.config.ts` |

`make setup` copies all three into place without overwriting anything.

### 6.1 Validation at import, not at use

`app/core/config.py` is a pydantic `BaseSettings` model. Every value is parsed
and validated when the module is imported, so a misconfigured deployment stops
at startup rather than failing on whichever request first touches the bad
value. A `model_validator` adds production guards: outside `local`, the process
refuses to start if `SECRET_KEY` is under 32 characters, `ID_HASH_PEPPER` still
holds its development default, `COOKIE_SECURE` is false, `ALLOWED_HOSTS`
contains `*`, or `DEBUG` is on.

### 6.2 The five that matter

| Variable | Why |
|---|---|
| `SECRET_KEY` | Unset, a random key is generated **per process**: every restart invalidates every token and two replicas never agree. Generate with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. |
| `ID_HASH_PEPPER` | Mixed in before hashing Aadhaar/PAN. Rotating it invalidates every stored hash. Effectively permanent once real data exists. |
| `COOKIE_SECURE` | `false` is correct on `http://localhost`; anywhere else it hands the refresh token to anyone on the network. |
| `API_ORIGIN` | No `NEXT_PUBLIC_` prefix, deliberately — server-side only. See §5.1. |
| `DEFAULT_METRIC_SRID` | `7755`. Areas and volumes computed in degrees are meaningless; geometry is *stored* in 4326 and *measured* in 7755. |

### 6.3 Full reference

Every key, with its default and a note on what it does, is in the three
`.example` files themselves rather than duplicated here — a table in a document
drifts from the code, a comment beside the default does not.

Grouped:

- **Application** — `PROJECT_NAME`, `API_V1_PREFIX`, `ENVIRONMENT`, `DEBUG`, `VERSION`
- **Security** — `SECRET_KEY`, `JWT_ALGORITHM`, `JWT_ISSUER`, `JWT_AUDIENCE`, the four token lifetimes, `ID_HASH_PEPPER`, five `ARGON2_*`, `MAX_LOGIN_ATTEMPTS`, `LOCKOUT_MINUTES`, `PASSWORD_MIN_LENGTH`
- **CORS** — `BACKEND_CORS_ORIGINS`, `ALLOWED_HOSTS` (both accept CSV or JSON)
- **Database** — six `POSTGRES_*`, five `DB_*`
- **Redis** — `REDIS_URL`, three `RATE_LIMIT_*`
- **Email** — six `SMTP_*`/`EMAIL_*`, `FRONTEND_URL`
- **Geospatial** — `STORAGE_SRID`, `DEFAULT_METRIC_SRID`, `DEFAULT_VERTICAL_DATUM`, `GEOM_TOLERANCE_M`
- **Cookies** — `COOKIE_NAME_REFRESH`, `COOKIE_DOMAIN`, `COOKIE_SECURE`, `COOKIE_SAMESITE`

### 6.4 Secrets in production

`.env` files are a development affordance. In production, inject
`SECRET_KEY`, `ID_HASH_PEPPER`, `POSTGRES_PASSWORD` and `SMTP_PASSWORD` from
the platform's secret store — Docker secrets, Kubernetes `Secret`, AWS Secrets
Manager. Pydantic reads plain environment variables, so no application change
is needed. `SecretStr` keeps these values out of `repr()` and tracebacks, but
that is defence in depth, not a substitute for keeping them off disk.

---

Next: [DEPENDENCIES.md](DEPENDENCIES.md) · [INSTALLATION.md](INSTALLATION.md)
