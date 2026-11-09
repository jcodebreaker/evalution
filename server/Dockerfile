# The page, built here so running this needs no Node on the host.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS app
# The uv CI uses, so it reads the lock file as CI does.
COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /usr/local/bin/uv
WORKDIR /app/server
COPY server/pyproject.toml server/uv.lock ./
# With the Postgres driver, so the same image runs on SQLite or, given DATABASE_URL, Postgres.
RUN uv sync --frozen --no-dev --extra postgres --no-install-project
COPY server/evalution ./evalution
RUN uv sync --frozen --no-dev --extra postgres
COPY library /app/library
COPY --from=web /web/dist /app/web/dist
WORKDIR /app
ENV EVALUTION_CONFIG=/app/evalution.yaml
ENV PATH=/app/server/.venv/bin:$PATH
EXPOSE 8080
CMD ["evalution", "serve", "--host", "0.0.0.0", "--port", "8080"]

# With UI testing (ADR 0010): `docker build --target page`. Playwright, its Chromium with the
# system libraries it needs, and ffmpeg for a try's video: much bigger, so not the default.
FROM app AS page
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app/server
RUN uv sync --frozen --no-dev --extra postgres --extra page \
    && uv run --no-sync playwright install --with-deps chromium ffmpeg \
    && chmod -R a+rX /ms-playwright
WORKDIR /app

# The default image, without a browser.
FROM app
