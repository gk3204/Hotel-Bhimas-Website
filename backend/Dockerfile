# Multi-stage build for Hotel Bhimas Backend
FROM python:3.11-slim as builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies globally (not --user)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Install runtime dependencies only.
#
# postgresql-client-18 from PGDG, NOT Debian's generic `postgresql-client`.
# Bookworm ships client 15, and pg_dump REFUSES to dump a server newer than itself:
#     pg_dump: error: aborting because of server version mismatch
# The managed Postgres is 18.6, so the generic package silently gives you a backup endpoint
# that 500s on every call — /admin/backup/export was broken exactly this way until a real
# BhimasBackup run surfaced it. A newer pg_dump reads older servers fine, so pinning 18 is
# safe if the database is ever moved back a version.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
         -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    # Read the codename from the base image rather than hardcoding it. python:3.11-slim moved
    # from bookworm to trixie, and a stale codename here does not fail loudly — apt reports
    # "held broken packages", which reads like a dependency problem rather than a wrong repo.
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
         > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-18 \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder (global site-packages)
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH=/usr/local/bin:$PATH

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

# Copy application code
COPY --chown=appuser:appuser . .

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Run the API. 2 workers suits a single hotel: fewer duplicate in-process schedulers and roughly
# half the memory of 4. Raise to 4 only if you run many concurrent desk stations. Only the LAST CMD
# takes effect, so there is exactly one now (the old duplicate --workers 4 line was dead).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
