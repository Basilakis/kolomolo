# Single image used for BOTH the serving app and (at scale) the ingestion workers.
# Same code, different entrypoint/command — keeps build + deploy simple.
FROM python:3.11-slim

# System deps for PyMuPDF / pdfplumber (PDF rendering) and healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for layer caching.
COPY requirements.txt pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e .

# App + eval question set.
COPY app ./app

# Embedding model for the baseline is downloaded on first use; pre-cache at build time
# in production to avoid a cold-start download (left out here to keep the image small).

EXPOSE 8501

# Default = serving frontend. Override `command:` for ingestion / workers (see docker-compose).
CMD ["streamlit", "run", "app/streamlit_app.py", \
     "--server.address=0.0.0.0", "--server.port=8501"]
