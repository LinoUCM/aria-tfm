# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# ARIA backend — imagen de producción (multi-stage).
#   - Stage `builder`: instala las dependencias en un venv aislado, con las
#     herramientas de compilación (build-essential, libpq-dev).
#   - Stage `runtime`: copia solo el venv ya construido; sin toolchain de
#     compilación. Arranca uvicorn SIN --reload y con varios workers.
# El .env NUNCA se copia a la imagen: se inyecta por env_file en el compose.
# ─────────────────────────────────────────────────────────────────────────────

# ---- builder ---------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ---- runtime -------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    ENVIRONMENT=production

# curl: solo para el HEALTHCHECK. Sin build-essential ni libpq-dev en la
# imagen final (psycopg2-binary y asyncpg traen su propia libpq).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

# Directorios de datos en runtime (montados como volumen en el compose).
RUN mkdir -p /app/uploads /app/storage/postmortems /app/storage/runbooks

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# 2 workers: razonable para 2 vCPUs dejando margen para Postgres/Ollama en la
# misma VM. Sin --reload.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
