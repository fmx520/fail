# Merged iPhone Duo watcher + buyer — single Docker image
#
# WHAT THIS IS
# One image that hosts BOTH halves of the iPhone Duo auto-checkout system
# in a single Render background worker (see merged-render.yaml):
#   - the stdlib watcher (watcher.py): polls Apple's public pickup-message
#     endpoint, one request per poll, for the four Long Island stores.
#     It has NO third-party dependencies (stdlib only).
#   - the Playwright buyer (buyer.py + buyer_config.py): drives Apple
#     checkout in Chromium. It needs Playwright + Chromium + system libs,
#     which is why the old 0.5 CPU / 512 MB Python worker could not host
#     it and why this image is Docker-based.
#   - the merged service entrypoint (merged_service.py): wires the watcher
#     to the buyer in one process. That file is delivered SEPARATELY (by a
#     sibling workstream, in this same merged/ directory) and is NOT part
#     of this packaging deliverable. Do not build this image until
#     merged_service.py exists in the build context, or the COPY below
#     fails — that failure is intentional, not something to work around
#     by dropping the merged entrypoint.
#
# BUILD CONTEXT — VERIFIED AGAINST THE REPO LAYOUT (2026-11-01)
# fmx520/fail repo root contains: watcher.py, checkout.py, render.yaml,
# schema.sql, README.md, .env.example, plus buyer/ and merged/
# subdirectories. Build with the context set to the REPO ROOT:
#   docker build -f merged/merged.Dockerfile -t iphone-duo-merged .
# Render's blueprint must likewise use dockerContext: . (repo root)
# and dockerfilePath: ./merged/merged.Dockerfile (see
# merged-render.yaml). Every COPY below is relative to that repo-root
# context: watcher.py / checkout.py at root, buyer/buyer.py at
# buyer/, merged/merged_service.py at merged/. Do NOT change
# dockerContext to ./merged — that would break every COPY line.
# Note the flattened layout at runtime: buyer.py and
# merged_service.py are both copied to /app, so merged_service.py
# finds buyer.py next to itself (see its import search order).
#
# RESOURCE FIT — 1 CPU / 2 GB (Render Standard, confirmed 2026-11-01)
# Chromium alone wants ~300-500 MB RSS before it loads a page, and Apple
# checkout pages are heavy JS (easily 500 MB-1 GB with the Playwright
# driver + Python on top). 2 GB is the FLOOR that works, not headroom:
# run exactly ONE Chromium instance (the merged service must never launch
# a second browser in parallel), and treat an OOM kill mid-checkout as
# the known risk of Standard. The buyer spec's preferred size was Pro
# (2 CPU / 4 GB); Parker confirmed Standard anyway, so this image does
# NOT silently upgrade the plan — if OOM kills appear in logs, the fix
# is a plan bump, not a second service.
#
# SAFETY DEFAULTS — baked into the image, overridable by Render env vars
# DRY_RUN=true, LIVE_BUY_ENABLED=false, AUTO_CHECKOUT_ENABLED=false.
# Render envVars (merged-render.yaml) set the same values, so a deploy
# cannot accidentally go live: live buying needs an explicit, deliberate
# env change (DRY_RUN=false AND LIVE_BUY_ENABLED=true AND
# AUTO_CHECKOUT_ENABLED=true) plus a passed supervised dry run.
# Starting the container NEVER attempts a checkout: CMD runs the merged
# service in its default (full) mode — watcher polling + warm idle
# buyer, no Apple checkout requests until a watcher hit signals the
# buyer. The service's --warm flag is a SEPARATE warm-only test mode
# (buyer idle only, watcher NOT polling, no Apple requests at all)
# and must NOT be used as the production CMD: a --warm container
# watches nothing. See merged_service.py main() for both modes.
#
# NO SECRETS IN THIS IMAGE
# No credentials, tokens, keys, card numbers, or Apple session data are
# copied in, baked in, or read from here. DATABASE_URL, PUSHOVER_TOKEN,
# and PUSHOVER_USER are Render dashboard env vars, set by Parker at
# migration time (see MIGRATION.md). The Apple session comes from the
# user's own signed-in browser profile via the merged service's profile
# strategy — never from a credential stored in this image.

FROM python:3.12-slim

# Playwright browser location (shared, readable by the non-root user).
# PYTHONUNBUFFERED keeps Render logs streaming instead of buffered.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DRY_RUN=true \
    LIVE_BUY_ENABLED=false \
    AUTO_CHECKOUT_ENABLED=false

WORKDIR /app

# Install dependencies BEFORE copying app code so the heavy Chromium
# layer is cached and rarely rebuilt.
#   - playwright>=1.44: same floor as buyer/requirements.txt.
#   - psycopg[binary]: OPTIONAL for the watcher. watcher.py is stdlib-only
#     and runs fine without it; psycopg only enables its existing
#     drops-table write path (schema.sql) when DATABASE_URL is set.
#     Without it, watcher.py logs [DB WARN] and keeps polling — the DB
#     must never kill the watcher. Installing the binary wheel here
#     avoids a compiler toolchain in the image.
RUN pip install --no-cache-dir "playwright>=1.44" "psycopg[binary]" \
    && python -m playwright install --with-deps chromium

# Non-root where possible: Chromium runs fine unprivileged, and neither
# the watcher nor the buyer ever needs root. The profile directory must
# be writable by this user at runtime (warm persistent session).
RUN groupadd -r duo && useradd -r -g duo -d /home/duo duo \
    && mkdir -p /home/duo/profile /app \
    && chown -R duo:duo /home/duo /app

# App code. Order matters only for layer caching; all of these are
# required at runtime:
#   watcher.py, checkout.py  — the watcher (checkout.py is imported by
#     watcher.py only when AUTO_CHECKOUT_ENABLED=true; it is included so
#     the merged service has the same code the live worker runs today).
#   buyer/buyer.py, buyer/buyer_config.py — the Playwright buyer.
#   merged/merged_service.py — the merged entrypoint (SIBLING DELIVERABLE;
#     see header). If this COPY fails, the sibling file is missing —
#     do not "fix" it by pointing CMD at watcher.py or buyer.py alone,
#     or the merged service is silently not merged.
COPY watcher.py checkout.py ./
COPY buyer/buyer.py buyer/buyer_config.py ./
COPY merged/merged_service.py ./

USER duo

# Production startup: full merged service (watcher + warm buyer),
# dry-run gated by env (DRY_RUN=true, LIVE_BUY_ENABLED=false,
# AUTO_CHECKOUT_ENABLED=false). Starting the container NEVER attempts
# a checkout: the buyer stays warm/idle until a watcher hit signals
# it, and the dry-run gate stops before Place Order in any case.
# Do NOT add --warm here: --warm is the warm-only test mode
# (buyer idle only, watcher NOT running, no Apple requests), so a
# --warm container would watch nothing. The env gates above, not a
# flag, are what stop a container restart from turning into a
# checkout attempt.
CMD ["python3", "merged_service.py"]
