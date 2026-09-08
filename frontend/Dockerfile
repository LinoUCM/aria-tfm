# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# ARIA frontend — imagen de producción (multi-stage).
#   - `deps`      : instala TODAS las dependencias (incluye dev) para el build.
#   - `builder`   : `next build` con NEXT_PUBLIC_API_URL inyectado (Next lo
#                    "hornea" en el bundle en tiempo de build, no en runtime).
#   - `prod-deps` : reinstala solo dependencias de producción.
#   - `runner`    : sirve con `next start` (nunca `next dev`).
#
# NEXT_PUBLIC_API_URL por defecto = "/api": ruta RELATIVA. El navegador llama a
# `/api/...` sobre el mismo origen y Caddy la reescribe a `backend:8000`. Así el
# mismo bundle vale para acceso por IP (http) y, más tarde, por dominio (https),
# sin reconstruir la imagen.
# ─────────────────────────────────────────────────────────────────────────────

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_URL=/api
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
RUN npm run build

FROM node:20-alpine AS prod-deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

COPY --from=prod-deps /app/node_modules ./node_modules
COPY --from=builder   /app/.next         ./.next
COPY --from=builder   /app/package.json  ./package.json
COPY --from=builder   /app/next.config.js ./next.config.js

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD wget -q --spider http://localhost:3000/login || exit 1

CMD ["npm", "run", "start"]
