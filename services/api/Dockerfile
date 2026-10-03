FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
# LightGBM needs the OpenMP runtime, which the slim image does not ship.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 pinero

# Development image: includes test and lint tooling; source is bind-mounted by Compose.
FROM base AS dev
COPY pyproject.toml ./
COPY app ./app
RUN pip install -e ".[dev]"
COPY . .
USER pinero
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Production image: runtime dependencies only. Run migrations as a separate release step
# (`alembic upgrade head`); on Railway that is the service's preDeployCommand.
#
# Binds :: rather than 0.0.0.0 because Railway's private network is IPv6; a dual-stack
# socket still accepts IPv4, so this also works under Compose and any IPv4-only proxy.
# Honours $PORT when the platform assigns one, defaulting to 8000.
#
# --forwarded-allow-ips='*' trusts X-Forwarded-* from any caller. That is safe only while
# nothing but your own proxy can reach this container: on Railway keep the API private
# (no public domain) so it is reachable solely over the private network. If you do attach
# a public domain, narrow this to your proxy's addresses.
FROM base AS prod
COPY pyproject.toml ./
COPY app ./app
RUN pip install .
# Bake the open embedding model (BGE-small, ~130 MB) into the image, so filing search never
# downloads it at request time and works without outbound access to Hugging Face.
ENV EMBEDDING_CACHE_DIR=/app/models
RUN python -m app.cli prepare-embeddings && chown -R pinero:pinero /app/models
COPY alembic.ini ./
COPY alembic ./alembic
USER pinero
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host :: --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
