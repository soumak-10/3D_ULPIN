# =============================================================================
#  Next.js image.
#
#  Build from the repository root:
#      docker build -f infra/docker/web.Dockerfile --target production \
#        --build-arg API_ORIGIN=http://api:8000 -t ulpin-web .
# =============================================================================

# -----------------------------------------------------------------------------
FROM node:22-alpine AS base

# Next's SWC binaries are glibc-linked in some builds; libc6-compat is the
# alpine shim that keeps them loadable.
RUN apk add --no-cache libc6-compat
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# -----------------------------------------------------------------------------
FROM base AS deps

COPY apps/web/package.json apps/web/package-lock.json* ./
# `npm ci` when there is a lockfile, `npm install` when there is not. A lockfile
# is the right answer; this keeps a fresh checkout building either way.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# -----------------------------------------------------------------------------
FROM base AS development

COPY --from=deps /app/node_modules ./node_modules
COPY apps/web/ ./

EXPOSE 3000
CMD ["npm", "run", "dev"]

# -----------------------------------------------------------------------------
FROM base AS builder

# API_ORIGIN is read by next.config.ts at build time to wire the /api/v1
# rewrite. It is a server-side value — it is not inlined into the client bundle
# and is not a secret in the browser, because the browser never sees it.
ARG API_ORIGIN=http://api:8000
ENV API_ORIGIN=$API_ORIGIN

COPY --from=deps /app/node_modules ./node_modules
COPY apps/web/ ./

# The build type-checks and lints: next.config.ts sets ignoreBuildErrors and
# ignoreDuringBuilds to false, so a type error fails the image rather than
# shipping.
RUN npm run build

# -----------------------------------------------------------------------------
FROM base AS production

ENV NODE_ENV=production

# Production dependencies only — the build is already done.
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --omit=dev; else npm install --omit=dev; fi

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.ts ./

RUN addgroup --system --gid 10001 nodejs \
 && adduser  --system --uid 10001 nextjs \
 && chown -R nextjs:nodejs /app/.next
USER nextjs

EXPOSE 3000
ENV PORT=3000 HOSTNAME=0.0.0.0

CMD ["npm", "run", "start"]
