FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
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
# (`alembic upgrade head`) and set --forwarded-allow-ips to your proxy's addresses.
FROM base AS prod
COPY pyproject.toml ./
COPY app ./app
RUN pip install .
COPY alembic.ini ./
COPY alembic ./alembic
USER pinero
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
