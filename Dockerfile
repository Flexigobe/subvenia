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

# Install deps from a requirements list (no package install)
COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install \
      "fastapi[standard]>=0.115.0" \
      "uvicorn[standard]>=0.32.0" \
      "sqlalchemy>=2.0.36" \
      "alembic>=1.13.3" \
      "psycopg[binary]>=3.2.3" \
      "pydantic>=2.9.2" \
      "pydantic-settings>=2.6.0" \
      "jinja2>=3.1.4" \
      "httpx>=0.27.2" \
      "apscheduler>=3.10.4" \
      "python-multipart>=0.0.12" \
      "google-generativeai>=0.8.3" \
      "weasyprint>=63.0" \
      "pypdf>=4.0"

# Copy source code — app/ is importable from WORKDIR
COPY . /app

ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
