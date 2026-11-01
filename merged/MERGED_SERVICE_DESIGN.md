# Merged watcher + buyer service — design

One process, one Render Docker worker (Standard, 1 CPU / 2 GB, $25/mo
total, **replacing** the current $7/mo Python watcher — not in addition
to it). Code only in this directory; nothing here has been deployed,
no credentials were used, no order was placed, no Apple sign-in was
attempted.

## Files

| File | What it is |
|---|---|
| `merged_service.py` | The merged service. Watcher loop (main thread) + warm Playwright buyer (buyer thread), in-process signaling, all guards. |
| `requirements.txt` | `playwright` only (same as `buyer/requirements.txt`); Chromium is baked into the image at build time, never installed on worker start. |
| `MERGED_SERVICE_DESIGN.md` | This file. |

Untouched by design: `../watcher.py`, `../checkout.py`, `../buyer/`.
`merged_service.py` imports the buyer steps from `../buyer/buyer.py`
(and the store order from `../buyer/buyer_config.py`, with an identical
hardcoded fallback) — it does not copy them, so selector fixes made in
`buyer.py` during the dry run apply to the merged service too. The
watcher poll logic is mirrored (not imported) in `merged_service.py`
because `watcher.py`'s `main()` loop hardcodes its old Supabase/Pushover
hit handling; the poll, hit rule, backoff, and heartbeat semantics are
identical (see below).

## In-process signaling (replaces `buyer/SIGNALING.md` for this service)

The old two-service design needed a Supabase `drops` row claimed
atomically because two Render services share no memory. That whole
mechanism is **deleted** in the merged service — it was the round-trip
the merge exists to remove.

- The watcher thread is the only producer; the buyer thread the only
  consumer. They share one `queue.Queue(maxsize=1)` and three
  `threading.Event` latches (`checkout_in_progress`,
  `order_submitted`, `hard_stop`).
- The buyer thread blocks on `queue.get(timeout=300s)`. A blocking get
  returns the instant an item is enqueued, so wake latency is ~1 s
  (queue wake + first navigation from the already-warm browser) —
  there is no 2 s DB poll, no chat hop, no Pushover hop in the path.
  The 300 s timeout only produces a warm-alive log line, at the same
  cadence as the watcher heartbeat; it never touches Apple.
- One signal per poll, not per store: if several priority stores flip
  to available in the same poll, the watcher enqueues one signal for
  the first available store in priority order. The buyer re-verifies
  **all** priority stores in-browser anyway (try-next-store), so the
  signal store is a hint, not a commitment.
- One attempt per signal, ever. A hit arriving while an attempt is
  running or already queued is **dropped with a log line**, not queued
  behind it: a second attempt would be stale (drops last seconds) and
  one browser context cannot run two checkouts. The in-progress
  attempt already covers the other stores in-browser.
- Drop history is local JSONL (`drop_log.jsonl`, sold-out
  transitions) AND the Supabase `drops` table (same INSERT as
  watcher.py, best-effort, `[DB WARN]` on failure, never killing
  the watcher) — preserving the existing drops history. No Supabase
  READS happen anywhere in this service, and Supabase is never used
  for signaling — by design, so a DB outage can never block, delay,
  or fake a signal; it loses history rows at worst.

## Warm-browser lifecycle

- At startup the buyer thread launches **one** persistent Chromium
  context from `PROFILE_DIR` (the user's own pre-created signed-in
  session — this service never creates a session and never handles
  credentials) and **one** blank page, then idles. Warm mode makes
  **zero** Apple requests: no availability polling in the browser, no
  session-refresh navigation, no bag/configurator load.
- On a hit, the same warm page runs the buyer steps in order:
  session pre-check → configurator (512GB / Night Sky / connect-later /
  no trade-in) → AppleCare yearly → bag verification (MK1H4LL/A /
  $2,199) → pickup in priority order, earliest slot, try-next-store →
  default payment → review re-verification → dry-run gate **or**
  (live mode only) second review verification → single guarded
  Place Order click.
- After an attempt the page is reused for the next attempt (steps
  navigate it as needed). If the page died, it is recreated from the
  same context; if the context died, it is relaunched once for that
  attempt. A relaunch failure aborts **that attempt only** — the
  service stays warm and the next hit tries again. There is no tight
  relaunch loop.
- Playwright `sync_api` objects are only ever touched from the buyer
  thread (the thread that created them). The watcher thread touches
  only the queue and the Event latches, never a page. This is what
  makes the two-thread design safe.

## Session expiry / bot challenge / 2FA: hard stop, alert, no retry loop

These surface from `buyer.py` as aborts (`SESSION_EXPIRED`,
`BOT_CHALLENGE`, `TWO_FACTOR_PROMPT`). The merged service captures the
abort reason (by wrapping `buyer.abort` for the duration of the
attempt — buyer steps resolve `abort` from their module globals, so
the wrapper sees the exact reason), logs `NO ORDER PLACED` + reason,
sends a result notification (log-only unless push credentials are
configured), and then **latches `hard_stop` for the rest of the
process lifetime**:

- The failed attempt is never retried.
- Every later hit is logged as `HIT SUPPRESSED - HARD STOP` and never
  starts a checkout, until the operator restarts the service after
  re-signing in themselves.
- The watcher thread keeps polling throughout, and Duo Tracker
  remains the availability alert path — a hard stop loses no alerting,
  only auto-checkout.

Deliberately **not** hard-stopped: sold-out mid-checkout, selector
unverified, price/config/AppleCare/payment/total mismatch, and
unexpected checkout errors. Those end the attempt, log
`NO ORDER PLACED` + reason, and return the service to warm; a **new**
later hit (a new unavailable → available transition) may attempt
again. The same signal is never retried.

## Double Place Order guard (three layers)

1. `merged_service.py`'s process-lifetime `order_submitted` latch,
   set **before** the click is attempted.
2. `buyer.py`'s own `_ORDER_SUBMITTED` guard, also set before the click.
3. After any submit — including an **ambiguous** one (timeout /
   dropped connection / exception after the click started) — all
   later hits are suppressed and the operator is told to check Apple
   order history manually. An ambiguous outcome is never retried blind:
   a duplicate order is worse than a missed order.

Additional scope guard: the buyer only ever checks out `MK1H4LL/A`.
A watcher hit for any other part is logged as ignored, never
substituted. Pickup never falls back to shipping or another config.

## Dry-run gate, and one deliberate deviation from `buyer.py`

Default mode is dry-run (`DRY_RUN=true`, `LIVE_BUY_ENABLED=false`).
Live Place Order requires **both** `DRY_RUN=false` **and**
`LIVE_BUY_ENABLED=true` (buyer's `LIVE_MODE`). In dry-run mode a hit
runs to the review page, dumps review text to `DRY_RUN_REVIEW_TXT` and
a screenshot to `DRY_RUN_SCREENSHOT`, and ends before Place Order with
`NO ORDER PLACED`.

Deviation: `buyer.py` standalone **exits its process** on the dry-run
gate (and on every abort). The merged service converts those
`SystemExit`s into **attempt outcomes** and returns to warm watching.
Exiting the merged process on a dry run would stop the watcher — the
opposite of what a merged service is for. The no-order guarantee is
identical (Place Order is never clicked); only the process-survival
behaviour differs, and it is logged explicitly per attempt.

Related: a dry-run attempt leaves its items in the Apple bag, and this
service does **not** auto-clear the bag (no verified selector exists
for that). Restart the service and/or clear the bag after a dry run
before any live attempt; do not mix dry-run and live attempts in one
process without clearing the bag first.

`AUTO_CHECKOUT_ENABLED` is **not** consulted by the merged service.
The only Place Order gate is `LIVE_MODE`. In default mode, hits do
trigger dry-run flows to the review dump — that is the intended
dry-run behaviour. To stop attempts entirely, stop the service.

## Watcher behaviour preserved verbatim

- One stdlib `urllib` request per poll, all watched parts in it;
  12 s poll + 0–3 s jitter; 300 s heartbeat (extended here to also
  report buyer status: warm idle / busy / hard-stopped / unavailable /
  submitted).
- HTTP errors including 541: double the backoff, cap 300 s, keep prior
  state. A failed/blocked/unparseable check is **never** "sold out"
  and never a hit.
- Hit rule: `pickupDisplay != "unavailable"` **and**
  `messageTypes.regular.storeSelectionEnabled == true`, using Apple's
  own `storePickupProductTitle`.
- Availability alerts stay suppressed (CHECKOUT_ONLY semantics): the
  existing Duo Tracker app handles availability alerts; this service
  sends only checkout-result notifications, and a push is never proof
  of an order.

## Sharing one 1 CPU / 2 GB box

- Watcher cost is negligible: one short HTTPS request every ~12–15 s,
  sleeping the rest of the time (I/O releases the GIL, so it does not
  block the buyer thread).
- Browser idle cost is kept low on purpose: one context, one blank
  page, headless, plus `--disable-dev-shm-usage` (critical: default
  `/dev/shm` is small on small containers and Chromium crashes without
  it), `--disable-gpu`, `--no-first-run`, `--disable-extensions`.
- Checkout is the only CPU spike, and it is supposed to win: the
  watcher may slip a poll by a few seconds mid-checkout (its sleep is
  not pre-empted, just delayed), which is harmless — a delayed poll is
  never misread as sold-out, and the heartbeat may print late. The
  buyer steps carry their own 30–60 s timeouts, bounding any single
  attempt to roughly ~5 min worst case.
- Memory: one Chromium context idles in the low hundreds of MB on this
  configuration; the watcher is a few tens of MB. That fits 2 GB with
  headroom, provided no second context/page is ever opened — the code
  never opens one (page recreation replaces, never adds).

## What was NOT verified here (gaps for the parent coordinator)

1. **All Apple selectors are still TO-VERIFY-IN-DRY-RUN placeholders**
   in `buyer.py`. Until a supervised dry run replaces them with
   observed selectors, every attempt aborts with
   `SELECTOR_UNVERIFIED` by design. This merged code was
   `py_compile`d and import-tested only (see below); it has never
   driven a live Apple page.
2. **AppleCare yearly value ($199.99/yr) is still reported, not
   verified**, and no AppleCare part number exists or is used anywhere.
   The dry run must read the annual amount/term off the review page.
3. **Session behaviour is unverified**: no `PROFILE_DIR` /
   `storage_state` exists yet in this task, so warm-context launch,
   session pre-check pass/fail, and Render-datacenter-IP bot
   challenging of a real browser are all unproven. If the dry run is
   bot-challenged from Render IPs, the fallback remains a home
   residential-IP machine or Duo Tracker manual checkout — not stealth
   plugins/proxies as a first resort.
4. **Runtime behaviour was not executed**: this VM has no Playwright
   installed, and this task forbids deploy/sign-in/orders. The merged
   loop, queue wake latency (~1 s claim), warm idle cost, and
   hard-stop latching are therefore **code-reviewed, not run-proven**.
   The first real proof must be the supervised dry run (Path A/B per
   `buyer/DRY_RUN_TEST_PLAN.md`), on a host with Playwright + Chromium
   baked into the image.
5. **Deploy artefacts still needed (parent task, not this one)**:
   a single-service Dockerfile (Python 3.12-slim + Playwright +
   Chromium baked at build time, non-root, `merged_service.py` as the
   entrypoint) and a single Render blueprint (Docker background
   worker, Standard 1 CPU / 2 GB, `numInstances: 1`, persistent disk
   or encrypted `storage_state` for `PROFILE_DIR` per
   `buyer/PROFILE_STRATEGY.md`). Do **not** deploy the old
   `buyer/render-buyer.yaml` alongside the watcher `render.yaml` —
   that would create two buyers. One service, one blueprint.
6. **Bag-clear / restart discipline** (above) and **post-purchase
   shutdown** (stop the Render service after a purchase succeeds)
   are operator steps, not code.
