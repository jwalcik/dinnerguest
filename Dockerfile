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
        "google-genai>=0.1.0"

# Copy project definitions, documentation, source code, and test fixtures
COPY pyproject.toml README.md ./
COPY src/ src/
COPY tests/fixtures/ tests/fixtures/

# Install local package
RUN pip install --no-cache-dir .

ENV PYTHONPATH=/app/src

EXPOSE 8080

CMD ["python3", "-m", "uvicorn", "dinnerguest.server:app", "--host", "0.0.0.0", "--port", "8080"]
