FROM python:3.12-slim

# System dependencies: MySQL drivers and cryptography; libgl1/libglib2.0-0 for OpenCV (local OCR of supplier bills)
RUN apt-get update && apt-get install -y \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    curl \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (layer cache-friendly)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Non-root user for security
# /app/backups is created here so a new named volume mounted there inherits this ownership (backups are written as this user)
RUN useradd -m -u 1001 pharmacy && mkdir -p /app/backups && chown -R pharmacy:pharmacy /app
USER pharmacy

EXPOSE 8000

# Start the API only. Migrations never run on start: apply them explicitly with
#   docker compose run --rm --no-deps -e ALLOW_DB_MIGRATIONS=1 backend alembic upgrade head
# (see docs/DEPLOYMENT.md). The API refuses to start if the database revision differs from the code.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
