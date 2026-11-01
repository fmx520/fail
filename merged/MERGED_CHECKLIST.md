# Merged service — migration + dry-run checklist (coordinator synthesis)

Status: CODE AND DOCS ONLY. Nothing deployed, nothing pushed to GitHub,
no credentials used, no order placed, AUTO_CHECKOUT stays false.
Live mode requires BOTH DRY_RUN=false AND LIVE_BUY_ENABLED=true;
defaults in the merged service are DRY_RUN=true / LIVE_BUY_ENABLED=false.

## What was built (all in ~/workspace/iphone-duo-pickup/merged/)

- merged_service.py — one process: stdlib watcher loop (12s poll + 0–3s
  jitter, 300s heartbeat, backoff doubling to 300s on HTTP errors
  including 541, never treat an error as sold out) + warm Playwright
  buyer thread. In-process signaling via queue.Queue(maxsize=1):
  watcher hit -> buyer starts in ~1s from the warm browser.
  No Supabase claim round-trip, no chat/Pushover hop.
- MERGED_SERVICE_DESIGN.md — signaling, warm-browser lifecycle,
  hard-stop behaviour, CPU sharing on 1 CPU / 2 GB.
- merged.Dockerfile — python:3.12-slim, Playwright + Chromium,
  non-root, starts the merged service warm/dry-run.
- merged-render.yaml — ONE Docker background worker, plan standard
  (1 CPU / 2 GB), numInstances: 1, all 8 existing non-secret env vars
  preserved, plus DRY_RUN=true, LIVE_BUY_ENABLED=false,
  AUTO_CHECKOUT_ENABLED=false.
- MIGRATION.md — new-service migration (not in-place conversion),
  env-var carry-over, rollback, $25/mo total cost math.
- requirements.txt — playwright>=1.44 only.

## Verification done by the coordinator

- `python3 -m py_compile merged_service.py` — PASSES.
- Import-only smoke test reported by the service agent: driver
  imports, LIVE_MODE=False, store order correct, hit-rule True/False
  as expected. Main loop was NOT executed (would poll Apple and
  requires Playwright, not installed on this VM).
- Originals untouched: watcher.py, checkout.py, buyer/ were read only.

## Discrepancies — FIXED 2026-11-01 (see below; item 5 still open)

1. SIGNAL_SOURCE conflict — FIXED: merged-render.yaml now sets
   SIGNAL_SOURCE=in_process_queue and has NO SIGNAL_POLL_SECONDS
   var (there is no poll interval for a queue.get() wake).
   merged_service.py validates SIGNAL_SOURCE at import: any other
   value logs a [SIGNAL WARN] and is ignored, never falling back
   to a Supabase claim.
2. Supabase drops logging conflict — FIXED by restoring the write
   (preserves existing drops history): merged_service.py
   log_transition() now does local JSONL AND the same Supabase
   `drops` INSERT as watcher.py, best-effort, [DB WARN] on failure,
   never killing the watcher. Supabase is never READ and never
   used for signaling — a DB outage loses history rows only.
   The merged.Dockerfile already installs psycopg[binary], so the
   write path is available in the image. MIGRATION.md /
   MERGED_SERVICE_DESIGN.md updated to match.
3. `--warm` flag — FIXED by parsing it AND changing the CMD:
   merged_service.py now parses --warm as warm-only test mode
   (buyer thread only, watcher NOT polling, NO Apple requests,
   NO checkout). merged.Dockerfile CMD is now plain
   `python3 merged_service.py` (full service: watcher + warm
   buyer, dry-run gated). Do NOT re-add --warm to production CMD
   — a --warm container watches nothing.
4. Blueprint paths — VERIFIED against the repo layout:
   fmx520/fail repo root contains watcher.py, checkout.py,
   render.yaml, schema.sql, README.md, .env.example, plus buyer/
   and merged/. With the blueprint applied from repo root,
   dockerContext `.` = repo root and dockerfilePath
   `./merged/merged.Dockerfile` resolve correctly, and every
   Dockerfile COPY (watcher.py, checkout.py, buyer/, merged/)
   resolves from that same context. The blueprint file itself
   must be placed at the repo root as render.yaml at deploy
   time. merged_service.py's buyer import was also hardened for
   the Docker layout: buyer.py is copied flat into /app next to
   merged_service.py, so _HERE is now searched before
   _HERE.parent/buyer.
5. Docker build NOT run here: this VM has no Docker binary.
   First build step, on a Docker-capable machine from the REPO
   ROOT: `docker build -f merged/merged.Dockerfile -t iphone-duo-merged .`

## Migration steps Parker would need to approve next (NO deploy in this task)

1. Discrepancies 1–4 above are FIXED (2026-11-01); remaining
   pre-deploy verification is the Docker build (item 5) plus
   py_compile re-verification (done, see below).
2. Push merged/ files to GitHub fmx520/fail (separate approval;
   snapshot-push procedure, verify back from GitHub).
3. Create the NEW merged Docker worker in Render from the merged
   blueprint (Standard, 1 CPU / 2 GB, $25/mo total, prorated,
   cancel after a confirmed purchase). Do NOT create a second
   competing service alongside it, and do NOT delete the old
   worker yet.
4. Parker re-sets DATABASE_URL, PUSHOVER_TOKEN, PUSHOVER_USER
   on the NEW service in the Render dashboard (names only, values
   never in code/chat/files) — Render env vars do NOT auto-carry
   over between services. Confirm names present, AUTO_CHECKOUT_ENABLED=false.
5. Suspend the old worker srv-dbiulrm7bikc73du622g FIRST, then
   start the merged service. Verify: startup line, watcher
   heartbeat, buyer warm-ready log, no checkout attempt, no 541
   storm. Only then delete the old worker.
6. Rollback at any point: suspend/delete merged, resume old
   worker, flip live flags back to false.

## Supervised dry-run steps still needed (before ANY live flip)

1. Parker signs into Apple himself in the session/profile the
   buyer will use; confirms default payment; stays on standby
   for 2FA. The agent never handles the password or a 2FA code.
   Session expiry, bot challenge, or 2FA = hard stop, no retry.
2. Dry run, stopping before Place Order, in dry-run mode:
   configurator (512GB Night Sky, connect-later, no trade-in) ->
   AppleCare yearly -> bag check (MK1H4LL/A, $2,199) -> pickup
   in priority order Smith Haven R139 > Walt Whitman R068 >
   Roosevelt Field R060 > Manhasset R327, earliest slot,
   try-next-store on sellout -> default payment -> review page.
3. Verify review-page R1–R12: part, title, $2,199, AppleCare
   annual (exact value read off the UI — $199.99/yr is reported,
   NOT verified; no AppleCare part number is hard-coded),
   no trade-in, store, slot, default payment, total =
   phone + AppleCare yearly + tax. Any mismatch = abort,
   NO ORDER PLACED.
4. Path A (config actually available) is the only full pass.
   Path B (config unavailable) can only prove session,
   detection, sold-out handling, and the dry-run guard — never
   report Path B as a pass.
5. Gate to live ONLY after a Path A pass AND Parker's human
   confirmation of the review page: set DRY_RUN=false AND
   LIVE_BUY_ENABLED=true, redeploy, verify the first real hit
   against Apple's order status / confirmation email — never
   the push alone. Cancel the merged service after a confirmed
   purchase.
