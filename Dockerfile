# ── Stage 1: Build React frontend ──────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy frontend package files
COPY package.json package-lock.json* ./
RUN npm install

# Copy frontend source
COPY index.html vite.config.ts tsconfig.json ./
COPY src ./src

# Build frontend → outputs to /app/frontend/dist
RUN npm run build

# ── Stage 2: Python backend + static files ──────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend
COPY backend/server.py ./server.py

# Copy built frontend into backend/static (served by FastAPI)
COPY --from=frontend-builder /app/frontend/dist ./static

# Expose port
EXPOSE 8000

# Start server
CMD ["python", "server.py"]
