FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# WeasyPrint system deps + libpq for psycopg
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
      libgdk-pixbuf-2.0-0 libcairo2 fonts-dejavu-core \
      libpq5 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install deps first for layer caching
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

# Then copy code
COPY . /app

# Reinstall to register the package with the actual source
RUN pip install --no-deps .

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
