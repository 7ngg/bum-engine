# Geometry service. Build context = repo root (needs /schemas alongside).
#   docker build -f services/geometry/Dockerfile -t bum-geometry .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# deps first for layer caching
COPY services/geometry/requirements.txt /app/services/geometry/requirements.txt
RUN pip install --no-cache-dir -r /app/services/geometry/requirements.txt

# app + shared schemas (schema_io resolves /app/schemas from the package path)
COPY schemas /app/schemas
COPY services/geometry /app/services/geometry

WORKDIR /app/services/geometry
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
