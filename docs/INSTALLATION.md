# Installation

**§8 installation guide**

Two routes. Docker is the fast one and is what the rest of this guide assumes;
the manual route is there for when you need to attach a debugger.

Prerequisites are in [DEPENDENCIES.md §7.3](DEPENDENCIES.md#73-system-requirements).

---

## 8.1 Docker — five minutes

```bash
git clone <repository-url> ulpin-3d-system
cd ulpin-3d-system
make setup
```

`make setup` copies `.env.example`, `apps/api/.env.example` and
`apps/web/.env.example` into place. It never overwrites an existing file.

**Set `SECRET_KEY` in `.env` before starting — compose refuses to run without
it.**

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Paste the result into `.env`, then:

```bash
make dev
```

The first run pulls PostGIS (~600 MB), builds both images and bootstraps the
database. Three to five minutes. Afterwards, `make up` takes seconds.

| | |
|---|---|
| Application | <http://localhost:3000> |
| API docs (Swagger) | <http://localhost:8000/docs> |
| OpenAPI schema | <http://localhost:8000/api/v1/openapi.json> |
| Health | <http://localhost:8000/health> |

Without `make`:

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml --env-file .env up --build
```

### Watching the bootstrap

```bash
make logs s=postgres
```

Ends with `=== bootstrap complete ===`. The `api` service waits for the
postgres healthcheck, which queries `ulpin.units` rather than using
`pg_isready` — the latter returns true while init scripts are still running.

---

## 8.2 First login

The seed data includes five users:

| Email | Role |
|---|---|
| `admin@ulpin.gov.in` | Admin |
| `officer@ulpin.gov.in` | Property Officer |
| `owner@example.com` | Owner |
| `tenant@example.com` | Tenant |
| `auditor@ulpin.gov.in` | Auditor |

**Their password hashes are placeholders, not working Argon2id digests** —
a real hash cannot be committed to a public repository. Pick one of two
approaches.

**Either** register a fresh account at <http://localhost:3000/register> and
promote it:

```bash
make db-shell
```
```sql
UPDATE ulpin.users SET role = 'ADMIN', status = 'ACTIVE'
 WHERE email = 'you@example.com';
```

**Or** set a real hash on the seeded accounts:

```bash
docker compose -f infra/docker-compose.yml exec api \
  python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('DevPassword123!'))"
```
```sql
UPDATE ulpin.users SET password_hash = '<paste the $argon2id$... string>';
```

Role determines what is visible. Sign in as the officer to see verification,
fraud and registration; as an owner to see only your own units.

---

## 8.3 Verifying the install

1. **Dashboard** (`/`) — seven stat cards with non-zero counts.
2. **Buildings** (`/buildings`) — the seeded towers.
3. **3D viewer** (`/viewer`) — click a unit; the panel shows its ULPIN, owner, tenant, status and floor. Needs WebGL 2.
4. **Search** (`/search?q=Kulkarni`) — matches on owner name, with `matched_on` saying why each row matched.
5. **Verification** (`/verification`) — paste a ULPIN from the registry and run it. Five checks, one verdict.
6. **Fraud** (`/fraud`) — press **Run scan**. The seeds contain planted cases, so the five rules have something to find.

If (3) is blank but the rest works, the browser lacks WebGL 2 — check
<https://get.webgl.org/webgl2/>. If (6) finds nothing, the seeds did not load:
`make logs s=postgres`.

---

## 8.4 Manual installation

### Database

PostgreSQL 16 with PostGIS 3.4 **and SFCGAL**. Then, as a superuser:

```bash
createdb ulpin_db
cd database/schemas
for f in 00_extensions 01_types 02_tables 03_indexes 04_triggers 05_modules; do
  psql -d ulpin_db -v ON_ERROR_STOP=1 -f "$f.sql"
done
psql -d ulpin_db -v ON_ERROR_STOP=1 -f ../seeds/01_seed.sql
```

The order matters — types before tables, tables before indexes, indexes before
triggers. Then set a real password on the application role, which the schema
creates with a placeholder:

```sql
ALTER ROLE ulpin_app WITH PASSWORD 'your-local-password';
```

Confirm SFCGAL is present:

```sql
SELECT postgis_full_version();   -- must mention SFCGAL
```

### Backend

```bash
cd apps/api
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env               # set SECRET_KEY and POSTGRES_PASSWORD
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd apps/web
npm install
cp .env.example .env.local         # API_ORIGIN=http://localhost:8000
npm run dev
```

---

## 8.5 Everyday commands

```bash
make help          # all targets
make up            # start detached
make down          # stop; data survives
make logs s=api    # follow one service
make db-shell      # psql as superuser
make db-reset      # drop the volume, re-bootstrap  (DESTRUCTIVE)
make check         # lint + tests + production build, as CI runs it
```

---

## 8.6 Troubleshooting

**`SECRET_KEY` variable is not set** — compose is refusing on purpose. See §8.1.

**`api` restarts in a loop** — `make logs s=api`. Usually the database is not
ready (wait for the bootstrap), or `POSTGRES_PASSWORD` in `.env` disagrees with
the role. The bootstrap aligns them from `APP_DB_PASSWORD`, so a mismatch means
the volume predates the current `.env`: `make db-reset`.

**`type "geometry" does not exist`** — PostGIS is missing. Check the image tag
is `postgis/postgis:16-3.4`.

**`function st_volume does not exist`** — PostGIS without SFCGAL. Same fix.

**Port already allocated** — change `WEB_PORT` / `API_PORT` / `POSTGRES_PORT`
in `.env`.

**Login appears to do nothing** — almost always `COOKIE_SECURE=true` over plain
HTTP: the browser silently drops the refresh cookie. Set it `false` locally,
`true` behind TLS.

**Edits do not reload (Windows/macOS)** — bind mounts miss inotify.
`WATCHPACK_POLLING=true` is already set for `web`; for `api`, uvicorn's
`--reload` polls by default.

**`npm ci` fails in the image** — no lockfile yet. Run `npm install` once in
`apps/web` and commit `package-lock.json`.

**Database is wedged** — `make clean` deletes the volume and everything in it.

---

## 8.7 Going to production

Not a checklist to skim:

- [ ] `ENVIRONMENT=production`, `DEBUG=false` — the API refuses to start otherwise
- [ ] `SECRET_KEY` from a secret store, not a file
- [ ] `ID_HASH_PEPPER` set and **backed up with the database** — rotating it orphans every stored identity hash
- [ ] `COOKIE_SECURE=true`, TLS terminated at nginx
- [ ] `ALLOWED_HOSTS` explicit, never `*`
- [ ] `SEED_DATABASE=false` — demonstration data must never reach a real register
- [ ] `API_TARGET=production`, `WEB_TARGET=production`
- [ ] Redis reachable — otherwise rate limits are per-process and *N* replicas allow *N*× the rate
- [ ] `pg_dump -Fc` scheduled, and a restore actually tested
- [ ] The API connects as `ulpin_app`, never as the superuser: `ulpin_app` cannot `UPDATE` or `DELETE` `audit_logs`, which is the property that makes the audit trail worth keeping

---

[ARCHITECTURE.md](ARCHITECTURE.md) · [INFRASTRUCTURE.md](INFRASTRUCTURE.md) · [DEPENDENCIES.md](DEPENDENCIES.md)
