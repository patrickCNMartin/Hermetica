# A lean image for the Hermetica API, in plain Docker. Two stages, so uv and its
# caches never reach the image you ship.
#
# flake.nix builds the same thing with nix, which pins the whole closure (C
# libraries included) rather than only the Python packages. Use nix for releases
# if you want byte reproducibility; this file is the one that works anywhere and
# needs no nix knowledge.
#
# Neither image carries pandoc or TeX: scribe has no entry point yet and they
# cost ~500 MB. Add them to the runtime stage when it does.
#
# Build — pass the version, never tag `latest`. This project exists to make
# versions explicit; an image called `latest` is the same problem it solves:
#
#   VERSION=$(python -c "import tomllib;print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
#   docker build --build-arg VERSION=$VERSION -t hermetica:$VERSION .

ARG PYTHON_VERSION=3.13

# --- builder -----------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /src

# Dependencies first, alone on their own layer. It stays cached until
# pyproject.toml or uv.lock actually change, so editing code never re-resolves.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Then the project itself. --no-editable copies the packages into the venv
# instead of linking back to /src, which makes the venv self-contained: the
# runtime stage below needs the venv and nothing else.
COPY hermetica/ ./hermetica/
RUN uv sync --frozen --no-dev --no-editable

# --- runtime -----------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim

ARG VERSION
LABEL org.opencontainers.image.title="hermetica" \
      org.opencontainers.image.version="${VERSION}"

# The API writes only to /app/db. Nothing else needs to be writable.
RUN useradd --system --create-home --uid 10001 hermetica

WORKDIR /app
COPY --from=builder /src/.venv /app/.venv
RUN mkdir -p /app/db /app/logs && chown hermetica:hermetica /app/db /app/logs
USER hermetica

# DB and LOGS are deliberately unset: the code already defaults them to db/ and
# logs/ relative to the working directory, so /app/db and /app/logs mirror the
# repo layout without naming the paths twice. API_HOST is the one real override
# — the code defaults to 127.0.0.1, which no sibling container could reach, and
# it is safe only while the port stays unpublished. There is no auth.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    API_HOST=0.0.0.0

EXPOSE 8080
VOLUME ["/app/db", "/app/logs"]

# Configuration comes from the environment, not from a baked-in file:
#   docker run --env-file env/.env ...
# load_dotenv looks for /app/env/.env, finds nothing, and does not complain; a
# value already in the environment always wins over one in a file.
#
# Override the command to run a pull instead of the API:
#   docker run ... python -m chronos.chronos
CMD ["python", "-m", "api.server"]
