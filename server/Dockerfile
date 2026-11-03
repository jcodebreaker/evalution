# The page, built here so running this needs no Node on the host.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY server/evalution ./evalution
RUN uv sync --frozen --no-dev
COPY library /app/library
COPY --from=web /web/dist /app/web/dist
WORKDIR /app
ENV EVALUTION_CONFIG=/app/evalution.yaml
ENV PATH=/app/server/.venv/bin:$PATH
EXPOSE 8080
CMD ["evalution", "serve", "--host", "0.0.0.0", "--port", "8080"]
