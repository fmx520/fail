# Migration: old Python watcher worker → merged Docker worker

## The one-sentence version

Replace the live worker `iphone-duo-pickup` (service id
`srv-dbiulrm7bikc73du622g`, $7/mo, Python, `python3 watcher.py`) with
ONE new Docker worker from `merged-render.yaml` ($25/mo, Standard
1 CPU / 2 GB) — total bill after migration is **$25/mo, not $32/mo** —
and delete the old worker once the merged service is verified. Never
run both with checkout enabled.

## Current state (what is being replaced)

- Old service: `iphone-duo-pickup`, service id `srv-dbiulrm7bikc73du622g`,
  dashboard: https://dashboard.render.com/worker/srv-dbiulrm7bikc73du622g
- Blueprint: GitHub repo `fmx520/fail`, master branch, `render.yaml`
  at the project directory (the directory containing `watcher.py`).
- Runtime: Render Python env, no build step (stdlib only),
  startCommand `python3 watcher.py`, 0.5 CPU / 512 MB ($7/mo).
- Env vars on the old service: the 8 non-secret vars in `render.yaml`
  (`APPLE_LOCATION`, `AUTO_CHECKOUT_ENABLED`, `BUY_URL`,
  `CHECKOUT_ONLY`, `PARTS`, `POLL_INTERVAL_SECONDS`, `PUSH_PROVIDER`,
  `TARGET_STORES`) plus 3 dashboard-only secrets (`DATABASE_URL`,
  `PUSHOVER_TOKEN`, `PUSHOVER_USER`), set by Parker — values never in
  code, files, or chat.
- Checkout state at migration time: `AUTO_CHECKOUT_ENABLED=false`,
  checkout driver still a stub / unproven — nothing buys on a drop
  today, and the migration must not change that.

## New service vs in-place type change — which is safer

**Use a NEW service. Do not attempt an in-place conversion of the old
service from Python to Docker.**

Why new-service is safer:

1. **Reversibility.** The old worker stays intact (suspended, not
   deleted) until the merged service proves itself. If the merged
   deploy fails, rollback is "resume the old worker" — minutes, with
   a known-good config. An in-place conversion has no fallback: if
   the Docker build fails or the new runtime misbehaves, there is no
   running watcher to fall back to, and rebuilding the old Python
   service from scratch is slower than resuming it.
2. **No assumed Render behaviour.** Whether Render preserves a
   service id, dashboard env vars, and deploy history across a
   Python→Docker type change is not something this packaging verifies
   (no deploy was performed, and no Render docs were treated as a
   guarantee). The new-service path assumes nothing: env vars are
   re-set explicitly and verified by name on the new service.
3. **Clean billing cut.** Deleting the old service ends its $7/mo
   charge; a converted service leaves ambiguity about whether the
   plan change billed as an edit or a replacement.

The cost of the new-service path is one manual step Parker must do
(re-setting the 3 secrets — see below) and a short cutover window in
which the old worker is suspended before the merged service starts.
That window is deliberate: it is what prevents two watchers polling
Apple at once and two buyers racing on one hit.

## What happens to env vars

### Non-secret vars (8)

All 8 are already written, with identical values, in
`merged-render.yaml`, plus the merged service's own vars (`DRY_RUN`,
`LIVE_BUY_ENABLED`, `APPLECARE`, `TRADE_IN`, `PICKUP_SLOT`,
`SIGNAL_SOURCE=in_process_queue`, `PROFILE_STRATEGY`). There is
deliberately NO `SIGNAL_POLL_SECONDS` var: signaling is an
in-process queue with no poll interval (see merged-render.yaml).
Applying
the new blueprint sets them. After deploy, verify on the new service's
dashboard Environment page that all 8 match the old service's values
exactly — especially `PARTS=MK1H4LL/A` and
`TARGET_STORES=Smith Haven,Walt Whitman,Roosevelt Field,Manhasset`
(in priority order: Smith Haven R139 > Walt Whitman R068 >
Roosevelt Field R060 > Manhasset R327).

### Secret vars (3 + 1 possible)

Render dashboard env vars are **per-service**: creating a new service
does **not** automatically copy them from the old worker.

Parker must set these on the NEW service in the Render dashboard
(names only — values are never pasted into files, chat, or Git):

- `DATABASE_URL` — Supabase Postgres (drops table already created;
  no schema re-run needed).
- `PUSHOVER_TOKEN` — Pushover app token (result pushes only).
- `PUSHOVER_USER` — Pushover user key (result pushes only).
- `PROFILE_BLOB_KEY` — only if the merged service uses the
  `storage_state_blob` profile strategy (see
  `buyer/PROFILE_STRATEGY.md`); the blob itself lives in
  Supabase/object storage, not in env.

If Render offers an env-var carry-over during service creation,
treat it as unverified until each name shows as present (masked
value) on the new service. A missing `DATABASE_URL` means Supabase
drops history is not recorded (watcher logs `[DB WARN]` and keeps
polling — it does not crash; JSONL history still works). It does
NOT break buyer signaling: signaling is in-process, never via the
drops table. Missing Pushover vars mean no bought/failed result push.

### What must NOT be copied

No Apple credentials, no card numbers, no session data. The buyer
uses the default payment method already on Parker's Apple account
and a restored signed-in browser profile. There is no auto sign-in:
a missing/expired session is a hard stop (`SESSION_EXPIRED`), fixed
by Parker re-signing in himself — never by storing a password here.

## Migration sequence (CODE/DOCS ONLY so far — no step below has been run)

1. **Commit the packaging.** Push `merged/merged.Dockerfile`,
   `merged/merged-render.yaml`, `merged/MIGRATION.md`, and the
   sibling's `merged/merged_service.py` (+ `MERGED_SERVICE_DESIGN.md`)
   to `fmx520/fail` master. The Docker build fails without
   `merged_service.py` — that is intentional (see the Dockerfile
   header). Do not deploy from a branch that lacks it.
2. **Build-check (before any Render change).** On a machine with
   Docker, from the REPO ROOT (which contains watcher.py,
   checkout.py, buyer/, merged/): `docker build -f
   merged/merged.Dockerfile -t iphone-duo-merged .` This step could
   NOT be done in the packaging VM (no Docker binary there) — it is
   the first unverified item, and it must pass before step 3.
3. **Create the new service** from `merged-render.yaml` (new service
   name `iphone-duo-pickup-merged`). The blueprint file's contents
   must be applied from the repo root (Render reads its blueprint
   from the repo root): dockerContext `.` = repo root,
   dockerfilePath `./merged/merged.Dockerfile`. Confirm plan shows
   Standard (1 CPU / 2 GB) and `numInstances: 1`.
4. **Parker re-sets the secrets** on the new service (list above) and
   verifies each name present. Keep the old worker running during
   steps 3–4 — at this point the new service is not yet polling or
   buying (see step 5 ordering), so there is no race yet.
5. **Suspend the old worker FIRST, then start/verify the new one.**
   Order matters: suspend `srv-dbiulrm7bikc73du622g`, then let the
   merged service start. Never have both polling Apple with checkout
   enabled; during this cutover both are in safe mode anyway
   (`AUTO_CHECKOUT_ENABLED=false`, `DRY_RUN=true`,
   `LIVE_BUY_ENABLED=false`), but the suspend-first order removes
   even the double-polling rate-limit risk.
6. **Verify the merged service (warm / dry-run only):**
   - Logs show the watcher startup line (parts, stores, poll interval)
     and a `[HEARTBEAT]` with a successful Apple check.
   - Logs show the buyer warm-ready line (persistent browser warm,
     idle, no Apple checkout requests in warm mode).
   - No `[CHECKOUT RESULT]`, no Place Order attempt, no order.
   - If any of these are missing, stop at step 7 (rollback) — do not
     proceed to live flags.
7. **Delete the old worker** only after step 6 passes. Deleting (not
   just suspending) is what ends the $7/mo charge. Keep this order:
   verify merged → delete old. Deleting first and verifying second
   leaves no fallback.
8. **Later, separately, and only after a passed supervised dry run:**
   flip `DRY_RUN=false` + `LIVE_BUY_ENABLED=true` +
   `AUTO_CHECKOUT_ENABLED=true` on the merged service. That is NOT
   part of migration. Migration ends with all three safe.

## Why no second competing service must be created

Two services watching the same part at the same stores means:

- **Double Apple polling** from two IPs on the same endpoint — the
  fastest way to draw the HTTP 541 blocks the watcher already suffers
  from, which blinds both services exactly when a drop happens.
- **Two buyers racing on one hit** — the duplicate-order risk. A
  single instance (`numInstances: 1`) plus the merged service's own
  duplicate guard only works if there is one service to guard.
- **Double billing for no benefit** — $7 + $25 while both run, for a
  strictly worse outcome than $25 alone.

The old Duo Tracker Vercel app is NOT a competing service in this
sense: it is the alert source, and the Render worker is checkout-only
(`CHECKOUT_ONLY=true` suppresses duplicate availability alerts).
That division stays unchanged after migration.

## Rollback

Rollback is safe at any point before the old worker is deleted, and
it is two moves:

1. **Suspend or delete the merged service** (`iphone-duo-pickup-merged`).
2. **Resume the old worker** (`srv-dbiulrm7bikc73du622g`) and confirm
   its logs show normal polling + heartbeat.

Additionally, if live flags were ever flipped on the merged service,
flip them back first: `AUTO_CHECKOUT_ENABLED=false`,
`DRY_RUN=true`, `LIVE_BUY_ENABLED=false` — on the merged service
before suspending it, so a later accidental restart cannot buy.

Rollback after the old worker is deleted is harder: it means
re-creating the Python worker from `render.yaml` in Git and re-setting
its 3 dashboard secrets. That is why step 7 (delete old) comes only
after step 6 (merged verified). Do not delete the old worker "to keep
things tidy" before verification passes.

## Cost math

- Old worker: $7/mo (0.5 CPU / 512 MB Python worker).
- Merged service: $25/mo (Standard, 1 CPU / 2 GB Docker worker).
- **Total after migration: $25/mo** — the merged service replaces the
  old worker; it is not added to it. Anyone quoting $32/mo has
  double-counted.
- Render bills plan usage prorated (per-second granularity per
  Render's billing model): the overlap window in step 5, where both
  services briefly exist, costs a fraction of a day at both rates,
  not a full extra month. Verify the exact proration line on the
  Render billing page after cutover — this note states the $25/mo
  steady-state total, not a to-the-cent invoice prediction.
- **Cancel after a purchase succeeds.** Once Parker has a confirmed
  Duo order (confirmed in Apple's order status, not from a push
  alone), suspend/delete the merged service — there is nothing left
  for it to buy, and the $25/mo stops. Also delete the old worker if
  it was only suspended and never deleted. Do not leave either
  service running "just in case" after the purchase: a live buyer
  with no purchase left to make is pure risk and pure cost.

## What this packaging does NOT verify (gaps for the coordinator)

- **Docker build untested.** No Docker binary exists in the
  packaging VM, so `docker build` was never run. The Dockerfile is
  syntax-reviewed and mirrors the proven `buyer/Dockerfile` (same
  base image, same Playwright install, same non-root pattern, plus
  the watcher files), but the first real build may still surface
  issues (e.g. `psycopg[binary]` wheel availability on
  `python:3.12-slim`, Chromium download size/time on Render).
- **Merged service entrypoint — CLI now fixed.** `merged_service.py`
  accepts `--warm` as a warm-only test mode (buyer idle only,
  watcher NOT polling, no Apple requests); the production CMD is
  plain `python3 merged_service.py` (watcher + warm buyer, dry-run
  gated). Do NOT re-add `--warm` to the production CMD — a --warm
  container watches nothing.
- **Render blueprint application untested.** No deploy was performed
  (per task scope). Paths are now VERIFIED against the repo layout:
  blueprint applied from repo root, dockerContext `.` = repo root,
  dockerfilePath `./merged/merged.Dockerfile`, Dockerfile COPYs
  relative to repo root. The blueprint file itself must be placed
  at the repo root as render.yaml at deploy time — Render does not
  pick up a blueprint from merged/ on its own.
- **Profile persistence on the merged worker is unresolved by
  packaging alone.** `PROFILE_STRATEGY=storage_state_blob` is carried
  over from the buyer spec, but whether the blob restore works on
  the merged worker's ephemeral disk is the merged service's (and
  the profile strategy's) problem to prove in the dry run — a warm
  browser with no valid session must hard-stop, never auto sign-in.
- **AppleCare yearly selection unverified.** `$199.99/yr` is reported,
  not confirmed from the checkout UI. No part number is hard-coded;
  the dry run must read the actual annual option off the UI.
- **Render Standard pricing taken as confirmed** ($25/mo, 1 CPU /
  2 GB, per the task brief and the buyer spec's plan note). Re-check
  Render's pricing page at deploy time; if Standard has moved, the
  $25/mo total in this note moves with it.
