ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest

# ── Stage 1: Build the React frontend ──────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# ── Stage 2: Runtime image ──────────────────────────────────────────────────
FROM $BUILD_FROM

RUN apk add --no-cache nginx gettext jq curl python3 py3-pip

# Install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# Copy the built React app
COPY --from=frontend-builder /app/dist /var/www/html

# Copy Python backend
COPY backend.py /app/

# Copy nginx config template and s6-overlay service files
COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY rootfs /

WORKDIR /app
