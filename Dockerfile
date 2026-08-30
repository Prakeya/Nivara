# =============================================================================
# Nivara — multi-stage build
#   Stage 1: build the React frontend with Vite (node)
#   Stage 2: python runtime serving backend + built frontend/dist
# =============================================================================

# --- Stage 1: frontend build -------------------------------------------------
FROM node:20-alpine AS frontend

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: python runtime -------------------------------------------------
FROM python:3.12-slim

WORKDIR /app

# Install python deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ backend/
COPY --from=frontend /frontend/dist/ frontend/dist/
COPY data/evaluation/ data/evaluation/
COPY scripts/ scripts/
COPY tests/ ./tests/

ENV PYTHONPATH=/app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]