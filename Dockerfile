FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install core server and Google Cloud dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir \
        fastapi \
        uvicorn \
        pydantic \
        python-dotenv \
        "google-cloud-storage>=2.10.0" \
        "google-cloud-firestore>=2.13.0" \
        "google-cloud-aiplatform>=1.40.0" \
        "google-genai>=0.1.0" \
        "opentelemetry-api>=1.20.0" \
        "opentelemetry-sdk>=1.20.0" \
        "opentelemetry-exporter-gcp-trace>=1.6.0" \
        "pg8000>=1.30.0"

# Copy project definitions, documentation, source code, and test fixtures
COPY pyproject.toml README.md ./
COPY src/ src/
COPY tests/fixtures/ tests/fixtures/

# Verify canonical data files are present in the image
RUN test -f /app/src/dinnerguest/data/allergens_canonical.json && \
    test -f /app/src/dinnerguest/data/vibe_taxonomy.json && \
    test -f /app/src/dinnerguest/data/recipes_canonical.json

# Install local package in editable mode so /app/src is linked directly
RUN pip install --no-cache-dir -e .

ENV PYTHONPATH=/app/src

EXPOSE 8080

CMD ["python3", "-m", "dinnerguest.server"]
