FROM python:3.12-slim AS python-base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir \
    "openai>=1.0.0" \
    "tiktoken>=0.7.0" \
    "mcp>=1.26.0,<2.0.0" \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.30.0"

FROM python-base AS development

COPY Turning-Good-Agent/ ./Turning-Good-Agent/
COPY .skills/ ./.skills/

FROM node:22-alpine AS frontend-build

WORKDIR /build/web/frontend

COPY web/frontend/package.json web/frontend/package-lock.json ./
RUN npm ci

COPY web/frontend/ ./
RUN npm run build

FROM python-base AS runtime

COPY Turning-Good-Agent/ ./Turning-Good-Agent/
COPY .skills/ ./.skills/
COPY --from=frontend-build /build/web/static ./web/static/

EXPOSE 8000

CMD ["python", "-m", "Turning-Good-Agent", "web", "--host", "0.0.0.0", "--port", "8000"]
