FROM python:3.11

# Install Node.js pinned to the Vite engine floor (>=20.19 or >=22.12).
# The distro Node on debian-bookworm is too old for current Vite; pull
# Node 22.x from NodeSource so the build does not silently downgrade to
# an unsupported runtime, then assert the version at build time.
RUN apt-get update \
  && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
  && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && rm -rf /var/lib/apt/lists/* \
  && node --version \
  && node -v | grep -E '^v(20\.(19|[2-9][0-9])|2[2-9])'

# Copy uv from the official uv image
COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests first to take advantage of layer caching
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY backend/pyproject.toml backend/uv.lock ./backend/

# Install dependencies (Node + Python)
RUN npm ci \
  && npm ci --prefix frontend \
  && cd backend && uv sync --frozen

# Copy project source code
COPY . .

# The frontend API key is a Vite build-time variable and must be present in the build
# environment before `npm run build`; otherwise the bundled UI ships with an empty key and all
# /api/* requests return 401 when AUTH_ENABLED=true. Injected by docker compose via build-arg.
#
# Fail-loud at image-build time: an empty or template-placeholder
# value is rejected here rather than silently baked into the bundle.
# Set `VITE_API_KEY` in the root `.env` (which `docker compose build`
# threads through as a `--build-arg`) OR pass `--build-arg
# VITE_API_KEY=...` explicitly to `docker build`. To intentionally
# build an auth-disabled image, pass
# `--build-arg ALLOW_EMPTY_VITE_API_KEY=true` and run the container
# with `AUTH_ENABLED=false`.
ARG VITE_API_KEY=""
ARG ALLOW_EMPTY_VITE_API_KEY="false"
ENV VITE_API_KEY=$VITE_API_KEY

RUN if [ "$ALLOW_EMPTY_VITE_API_KEY" != "true" ]; then \
      if [ -z "$VITE_API_KEY" ]; then \
        echo "ERROR: VITE_API_KEY is required at image-build time." >&2; \
        echo "  - Set it in the repo-root .env (then run 'docker compose build')." >&2; \
        echo "  - Or pass --build-arg VITE_API_KEY=... to 'docker build'." >&2; \
        echo "  - To intentionally build an auth-disabled image, pass" >&2; \
        echo "    --build-arg ALLOW_EMPTY_VITE_API_KEY=true and run with" >&2; \
        echo "    AUTH_ENABLED=false." >&2; \
        exit 1; \
      fi; \
      case "$(printf '%s' "$VITE_API_KEY" | tr '[:upper:]' '[:lower:]')" in \
        change_me*|your_*|test-api-key) \
          echo "ERROR: VITE_API_KEY looks like a .env.example template placeholder." >&2; \
          echo "  Set a real value before building a production image." >&2; \
          exit 1; \
          ;; \
      esac; \
    fi

# Build the frontend static assets; production serves them via `vite preview` (the Vite dev server is no longer used)
RUN npm run build

EXPOSE 3000 5001

# Production startup — backend runs under gunicorn (single worker, multi-threaded, to
# preserve in-process simulation state); frontend serves the built assets via vite preview.
# For development, use `npm run dev` instead.
# Security prerequisite: SECRET_KEY and API_KEY must be set via .env (FLASK_DEBUG defaults to false).
CMD ["npm", "run", "start"]