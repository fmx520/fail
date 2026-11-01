#!/usr/bin/env python3
"""Merged iPhone Duo watcher + buyer service — ONE process, CODE ONLY.

This file merges the two previously separate components into a single
Render Docker worker process (Standard, 1 CPU / 2 GB, $25/mo total,
replacing — not in addition to — the current $7/mo Python watcher):

  * Thread 1 (main thread): the stdlib watcher loop from ../watcher.py,
    polling Apple's public pickup-message endpoint every 12 s (+ 0-3 s
    jitter), with the 300 s heartbeat, the doubling backoff capped at
    300 s on HTTP errors (including 541), and the never-treat-error-as-
    sold-out rule. Hit rule, copied verbatim in semantics:
    pickupDisplay != "unavailable" AND
    messageTypes.regular.storeSelectionEnabled == True.
  * Thread 2 (buyer thread): a warm, idle Playwright persistent Chromium
    context (the user's own pre-created signed-in session from
    PROFILE_DIR). It makes NO Apple requests while warm — it blocks on
    an in-memory queue — and wakes only when the watcher thread hands
    it a hit.

Signaling is IN-PROCESS ONLY: a ``queue.Queue(maxsize=1)`` plus
``threading.Event`` latches. There is deliberately NO Supabase claim
round-trip, NO chat hop, and NO Pushover hop between watcher and buyer
(the old two-service SIGNALING.md design is superseded for this merged
service; see MERGED_SERVICE_DESIGN.md). Because the buyer thread blocks
on ``queue.get()``, it wakes the instant a hit is enqueued — checkout
starts in ~1 s from the already-warm browser, not after a poll interval.

Fixed target spec (do not change without user confirmation):
  * iPhone Duo 512GB Night Sky, part MK1H4LL/A, $2,199, unlocked /
    connect later, no trade-in, default payment method on the account.
  * AppleCare+ on the YEARLY option. Reported $199.99/yr is UNVERIFIED
    until a supervised dry run reads it off the review page. NEVER
    hard-code an AppleCare part number (none is known/verified).
  * Pickup in priority order: Smith Haven R139 > Walt Whitman R068 >
    Roosevelt Field R060 > Manhasset R327; earliest pickup slot;
    try the next store if one sells out mid-checkout.
  * Total = phone + AppleCare yearly + tax. NO $2,199 total cap.

HARD SAFETY RULES (inherited from buyer/buyer.py, unchanged):
  1. DRY_RUN defaults to "true" and LIVE_BUY_ENABLED defaults to
     "false". Live Place Order requires BOTH DRY_RUN=false AND
     LIVE_BUY_ENABLED=true (buyer.py's LIVE_MODE). Anything else is
     dry-run mode: the attempt dumps the review page text + screenshot
     and ends BEFORE Place Order. NO ORDER PLACED.
  2. NEVER sign in. NEVER prompt for or handle a password / 2FA code.
     Session pre-check failure is a hard stop: SESSION_EXPIRED,
     BOT_CHALLENGE, or TWO_FACTOR_PROMPT latches the buyer into
     HARD_STOP for the rest of the process lifetime — no retry of that
     attempt, no retry loop, no re-attempt on later hits until the
     operator restarts the service after re-signing in. The watcher
     keeps polling and Duo Tracker remains the alert path.
  3. NEVER submit Place Order twice. Three layers: (a) this file's
     process-lifetime ``_ORDER_SUBMITTED`` latch, set BEFORE the click
     is attempted; (b) buyer.py's own ``_ORDER_SUBMITTED`` guard;
     (c) after any submit (or ambiguous submit outcome), all later
     hits are suppressed — the operator checks Apple order history
     manually. An ambiguous Place Order outcome (timeout / dropped
     connection after the click) is NEVER retried blind.
  4. All Apple selectors in buyer.py are TO-VERIFY-IN-DRY-RUN
     placeholders. A step with an unverified selector aborts with
     SELECTOR_UNVERIFIED rather than guessing. Until a supervised dry
     run replaces them with observed selectors, every attempt aborts
     by design — that is the safe state, not a bug.
  5. One checkout attempt per hit signal, ever. A hit arriving while a
     checkout is already in progress (or already queued) is dropped
     with a log line, NOT run in parallel: the in-progress attempt
     already re-verifies availability in-browser across all priority
     stores (try-next-store), and one browser context cannot run two
     checkouts. A sold-out / mismatch abort is NOT a hard stop: the
     service returns to warm and may attempt again on a NEW later hit
     (a new unavailable -> available transition), never by retrying
     the same signal.
  6. A Pushover/ntfy message is NEVER proof of an order. Result
     notifications (if push credentials are configured at all) say
     "check Apple order status" and are log-only otherwise. No
     credentials or secrets appear in this file; payment uses the
     DEFAULT method already on the Apple account.
  7. This file never places an order on import and never starts its
     loops on import. Loops run only under ``if __name__ == "__main__"``.

What this file deliberately does NOT do (deploy/integration gaps for
the parent coordinator — see MERGED_SERVICE_DESIGN.md):
  * No Dockerfile / render.yaml here (no deploy in this task).
  * No Supabase reads, and NO Supabase signaling. The old
    Supabase-claim design is NOT used: signaling is in-process only
    (SIGNAL_SOURCE=in_process_queue). Drop history IS written to
    Supabase (same drops table / INSERT as watcher.py, best-effort,
    DB errors logged as [DB WARN] and never killing the watcher) in
    addition to the local JSONL file, so the existing drops history
    is preserved. A DB outage loses history rows only — it can
    never block, delay, or fake a hit signal.
  * No bag clearing between attempts. A dry-run attempt leaves items
    in the bag; restart the service / clear the bag before any live
    attempt (see design doc).
  * AUTO_CHECKOUT_ENABLED is NOT consulted. The only Place Order gate
    is buyer.py's LIVE_MODE (DRY_RUN=false + LIVE_BUY_ENABLED=true).
    In default (dry-run) mode, hits DO trigger a dry-run checkout
    flow to the review dump — that is the intended dry-run behaviour.
    To stop attempts entirely, stop the service.

Environment (same names as watcher.py / buyer.py where they exist):
  APPLE_LOCATION, PARTS, TARGET_STORES, POLL_INTERVAL_SECONDS (12),
  HEARTBEAT_SECONDS (300), DROP_LOG, BUY_URL,
  PROFILE_DIR, DRY_RUN (true), LIVE_BUY_ENABLED (false),
  HEADLESS (true), DRY_RUN_SCREENSHOT, DRY_RUN_REVIEW_TXT,
  PUSH_PROVIDER, PUSHOVER_TOKEN, PUSHOVER_USER, NTFY_TOPIC
  (push credentials are optional: without them, results are log-only),
  SIGNAL_SOURCE (in_process_queue; validated, see below),
  DATABASE_URL (optional: Supabase drops history only, never signaling;
  without it or without psycopg, history is JSONL-only and a
  [DB WARN] is logged — the watcher keeps polling).
"""

from __future__ import annotations

import json
import os
import queue
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Buyer driver import (../buyer/buyer.py + buyer_config.py).
# ---------------------------------------------------------------------------
# buyer.py imports `from buyer_config import ...`, so its directory must
# be on sys.path. Importing buyer.py is side-effect-free for our purposes:
# it reads env vars at module level but does NOT import Playwright
# (deferred to runtime) and does NOT run its main() on import. If the
# buyer directory is absent (e.g. only merged/ was copied), the watcher
# still runs and every hit is logged as BUYER_UNAVAILABLE — the service
# never pretends checkout works when the driver is missing.

_HERE = Path(__file__).resolve().parent
# Buyer import search, in order:
#   1. _HERE itself — the Docker layout (merged.Dockerfile COPYs
#      buyer/buyer.py and merged_service.py flat into /app, so
#      buyer.py sits next to this file, not in a sibling buyer/).
#   2. _HERE.parent / "buyer" — the source-tree layout (merged/
#      next to buyer/ in the repo), used when running from source.
# Both are harmless to add; the first match wins. Without (1), the
# Docker image would rely on the script-directory sys.path fallback
# alone to find buyer.py — that works, but it is implicit and fragile.
_BUYER_DIR = _HERE.parent / "buyer"
for _p in (str(_HERE), str(_BUYER_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import buyer as buyer_driver  # noqa: E402  (../buyer/buyer.py)
    try:
        from buyer_config import STORES_IN_PRIORITY_ORDER  # noqa: E402
    except Exception:
        STORES_IN_PRIORITY_ORDER = None
except Exception as _buyer_import_error:  # buyer.py missing/unparseable
    buyer_driver = None
    STORES_IN_PRIORITY_ORDER = None
    _BUYER_IMPORT_ERROR = _buyer_import_error
else:
    _BUYER_IMPORT_ERROR = None

if not STORES_IN_PRIORITY_ORDER:
    # Fallback identical to buyer/buyer_config.py. Do not reorder without
    # user confirmation.
    STORES_IN_PRIORITY_ORDER = [
        {"name": "Smith Haven", "number": "R139"},
        {"name": "Walt Whitman", "number": "R068"},
        {"name": "Roosevelt Field", "number": "R060"},
        {"name": "Manhasset", "number": "R327"},
    ]

# ---------------------------------------------------------------------------
# Watcher config (mirrors watcher.py)
# ---------------------------------------------------------------------------

NY = ZoneInfo("America/New_York")
APPLE_URL = "https://www.apple.com/shop/retail/pickup-message"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 Mobile/15E148 Safari/604.1")

LOCATION = os.environ.get("APPLE_LOCATION", "11754")
PARTS = [p.strip() for p in os.environ.get("PARTS", "MK1H4LL/A").split(",") if p.strip()]
TARGET_STORES = [s.strip() for s in os.environ.get(
    "TARGET_STORES", "Smith Haven,Walt Whitman,Roosevelt Field,Manhasset").split(",") if s.strip()]
POLL = float(os.environ.get("POLL_INTERVAL_SECONDS", "12"))
HEARTBEAT_SECONDS = float(os.environ.get("HEARTBEAT_SECONDS", "300"))
BUY_URL = os.environ.get("BUY_URL", "https://www.apple.com/shop/buy-iphone/iphone-duo")
DROP_LOG = os.environ.get("DROP_LOG", str(_HERE / "drop_log.jsonl"))

PROFILE_DIR = os.environ.get("PROFILE_DIR", "./profile")
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() != "false"
DRY_RUN_SCREENSHOT = os.environ.get("DRY_RUN_SCREENSHOT", "./dry-run-review.png")
DRY_RUN_REVIEW_TXT = os.environ.get("DRY_RUN_REVIEW_TXT", "./dry-run-review.txt")

PUSH_PROVIDER = os.environ.get("PUSH_PROVIDER", "pushover").lower()

# Signaling contract, validated at import time (before any loop runs).
# The only supported value is "in_process_queue": the watcher thread
# hands hits to the buyer thread via queue.Queue(maxsize=1). There is
# no Supabase poll, no SIGNAL_POLL_SECONDS, and no fallback to a
# Supabase claim — a blueprint that still sets SIGNAL_SOURCE to
# "supabase_drops" is stale and must be fixed in the blueprint, not
# worked around here. A mismatched value is logged (not silently
# honoured) and in-process signaling is used regardless.
SIGNAL_SOURCE = os.environ.get("SIGNAL_SOURCE", "in_process_queue")
if SIGNAL_SOURCE not in ("in_process_queue", "in_process"):
    print(f"[SIGNAL WARN] SIGNAL_SOURCE={SIGNAL_SOURCE!r} is not supported; "
          f"merged service only supports in_process_queue signaling. "
          f"Normalizing to in_process_queue and using in-process queue "
          f"signaling (no Supabase fallback).",
          flush=True)
    SIGNAL_SOURCE = "in_process_queue"

# The single target part the buyer can actually check out. If PARTS ever
# lists more than this, only MK1H4LL/A signals the buyer; anything else
# is logged, never substituted into a checkout.
BUYER_PART = "MK1H4LL/A"


def now_ny() -> str:
    return datetime.now(NY).isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# In-process signaling + process-lifetime guards
# ---------------------------------------------------------------------------
# signal_queue: maxsize=1. The watcher thread is the only producer, the
# buyer thread the only consumer. A full queue means an attempt is
# already pending/in progress, so the new hit is dropped (logged), not
# queued behind it — drops last seconds, a queued second attempt would
# be stale and could only double-spend the same bag/context.

signal_queue: queue.Queue = queue.Queue(maxsize=1)
checkout_in_progress = threading.Event()
order_submitted = threading.Event()   # set BEFORE any Place Order click
hard_stop = threading.Event()          # session / bot / 2FA latch
hard_stop_reason = ""                  # written before hard_stop.set()
buyer_unavailable = threading.Event()
buyer_unavailable_reason = ""

# Abort reasons from buyer.py that latch HARD_STOP (no retry loop, no
# re-attempt on later hits until operator restart + re-sign-in).
HARD_STOP_MARKERS = ("SESSION_EXPIRED", "BOT_CHALLENGE", "TWO_FACTOR")


def buyer_status() -> str:
    if order_submitted.is_set():
        return "submitted (further hits suppressed; verify order history)"
    if hard_stop.is_set():
        return f"HARD_STOP ({hard_stop_reason})"
    if buyer_unavailable.is_set():
        return f"unavailable ({buyer_unavailable_reason})"
    if checkout_in_progress.is_set():
        return "checkout in progress"
    return "warm idle"


# ---------------------------------------------------------------------------
# Watcher: Apple pickup-message poll (stdlib only; mirrors watcher.py)
# ---------------------------------------------------------------------------

def fetch_availability():
    params = [("pl", "true"), ("mts.0", "regular"), ("location", LOCATION)]
    for i, part in enumerate(PARTS):
        params.append((f"parts.{i}", part))
    url = APPLE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_hit(avail) -> bool:
    if not avail:
        return False
    regular = (avail.get("messageTypes") or {}).get("regular") or {}
    return avail.get("pickupDisplay") != "unavailable" and regular.get("storeSelectionEnabled") is True


def parse(body):
    """-> {(store_name, part): (hit, title, quote)} for target stores only."""
    out = {}
    for store in (body.get("body") or {}).get("stores") or []:
        name = store.get("storeName")
        if name not in TARGET_STORES:
            continue
        for part, avail in (store.get("partsAvailability") or {}).items():
            regular = (avail.get("messageTypes") or {}).get("regular") or {}
            out[(name, part)] = (
                is_hit(avail),
                regular.get("storePickupProductTitle") or part,
                regular.get("storePickupQuote") or avail.get("pickupSearchQuote") or "",
            )
    return out


def log_transition(store: str, part: str, title: str, appeared=None, disappeared=None) -> None:
    """Drop history, sold-out transition only. Two sinks, same record:
    (1) local JSONL (always attempted), (2) the Supabase `drops` table
    (same INSERT as watcher.py, best-effort). This is HISTORY ONLY:
    signaling is in-process and never reads Supabase, so a DB failure
    loses a history row at worst — it can never block, delay, or fake
    a hit signal, and it never kills the watcher."""
    rec = {"store": store, "part": part, "config": title,
           "appeared_at_ny": appeared, "disappeared_at_ny": disappeared}
    try:
        with open(DROP_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception as e:  # history must never kill the watcher
        log(f"[DROP LOG WARN] {e}")
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            import psycopg  # type: ignore
            with psycopg.connect(db_url) as conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO drops (store, part, config, appeared_at, disappeared_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (store, part, title, appeared, disappeared))
        except Exception as e:  # DB must never kill the watcher
            log(f"[DB WARN] {e}")


def send_result(store: str, title: str, success: bool, detail: str) -> None:
    """Checkout-result notification, best-effort and NEVER proof of an
    order. Log-only unless push credentials are configured; any push
    failure is logged, never raised, and never retried in a loop."""
    detected = now_ny()
    verdict = "BOUGHT (verify in Apple order history)" if success else "CHECKOUT FAILED"
    message = (f"{verdict}: {title} at Apple {store} — {detected} ET. {detail} "
               f"Check your Apple order status to confirm; do not assume a "
               f"push alone means an order exists.")
    log(f"[CHECKOUT RESULT] {detected} {store} {title} success={success} :: {detail}")
    try:
        if PUSH_PROVIDER == "ntfy":
            topic = os.environ.get("NTFY_TOPIC")
            if not topic:
                log("[RESULT PUSH SKIPPED] NTFY_TOPIC not configured; log-only.")
                return
            req = urllib.request.Request(
                topic, data=message.encode(),
                headers={"Title": f"iPhone Duo checkout: {store}", "Priority": "max",
                         "Tags": "iphone,apple", "Click": BUY_URL},
                method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
        else:  # pushover
            token = os.environ.get("PUSHOVER_TOKEN")
            user = os.environ.get("PUSHOVER_USER")
            if not token or not user:
                log("[RESULT PUSH SKIPPED] Pushover credentials not configured; log-only.")
                return
            data = urllib.parse.urlencode({
                "token": token, "user": user,
                "title": f"iPhone Duo checkout — {store}",
                "message": message,
                "priority": "2", "retry": "30", "expire": "600",
                "url": BUY_URL, "url_title": "Check Apple order status",
                "sound": "siren",
            }).encode()
            req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=data)
            with urllib.request.urlopen(req, timeout=15) as resp:
                resp.read()
    except Exception as e:
        log(f"[RESULT PUSH WARN] {e} (result above is log-only)")


def _priority_index(store_name: str) -> int:
    for i, s in enumerate(STORES_IN_PRIORITY_ORDER):
        if s["name"] == store_name:
            return i
    return len(STORES_IN_PRIORITY_ORDER)


def watcher_loop() -> None:
    """Main-thread loop. Identical poll/backoff/heartbeat semantics to
    watcher.py; the only behavioural change is what a hit does: instead
    of a Supabase row / Pushover availability alert, it enqueues ONE
    in-process signal (first available store in priority order) for the
    buyer thread. Availability alerts stay suppressed — the existing
    Duo Tracker app handles those (CHECKOUT_ONLY semantics)."""
    state = {}   # (store, part) -> (hit, title, appeared_at_ny)
    backoff = POLL
    last_heartbeat = time.monotonic()
    log(f"[merged] Watching {PARTS} at {TARGET_STORES} from {LOCATION}, poll={POLL}s, "
        f"buyer part={BUYER_PART}, stores priority="
        f"{[s['name'] + ' ' + s['number'] for s in STORES_IN_PRIORITY_ORDER]}")
    while True:
        try:
            current = parse(fetch_availability())
        except urllib.error.HTTPError as e:
            # 429 / 541 (Apple's block page) / 503: back off, keep state.
            # An error is NEVER "sold out" and NEVER a hit.
            log(f"[CHECK FAILED] HTTP {e.code} at {now_ny()} — backing off to "
                f"{min(backoff * 2, 300):.0f}s")
            backoff = min(backoff * 2, 300)
            time.sleep(backoff)
            continue
        except Exception as e:
            log(f"[CHECK FAILED] {e} at {now_ny()} — backing off")
            backoff = min(backoff * 2, 300)
            time.sleep(backoff)
            continue
        backoff = POLL  # a good check clears the backoff
        if time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
            hits = sum(1 for v in current.values() if v[0])
            log(f"[HEARTBEAT] {now_ny()} last Apple check OK, "
                f"{hits}/{len(current)} target store/configs available, "
                f"buyer: {buyer_status()}")
            last_heartbeat = time.monotonic()
        new_hits = []  # (store, title) newly available this poll
        for key, (hit, title, quote) in current.items():
            store, part = key
            prev_hit, _, appeared = state.get(key, (False, title, None))
            if hit and not prev_hit:
                state[key] = (True, title, now_ny())
                if part == BUYER_PART:
                    new_hits.append((store, title))
                else:
                    log(f"[HIT IGNORED] {now_ny()} {store} {title} :: part {part} "
                        f"is not the buyer part {BUYER_PART}; no checkout attempted.")
            elif not hit and prev_hit:
                log_transition(store, part, title, appeared=appeared, disappeared=now_ny())
                log(f"[SOLD OUT] {now_ny()} {store} {title} (was up since {appeared})")
                state[key] = (False, title, None)
            else:
                state[key] = (hit, title, appeared)
        if new_hits:
            # One signal per poll, first available store in priority
            # order. The buyer re-verifies every priority store
            # in-browser anyway (try-next-store), so the signal store
            # is a hint, not a commitment.
            new_hits.sort(key=lambda st: _priority_index(st[0]))
            store, title = new_hits[0]
            available = [s for s, _ in new_hits]
            if order_submitted.is_set():
                log(f"[HIT SUPPRESSED - ALREADY SUBMITTED] {now_ny()} {store} {title} :: "
                    f"an order was already submitted this process lifetime; "
                    f"verify Apple order history manually, do not re-attempt.")
            elif hard_stop.is_set():
                log(f"[HIT SUPPRESSED - HARD STOP] {now_ny()} {store} {title} :: "
                    f"buyer hard-stopped ({hard_stop_reason}); restart after re-sign-in.")
            elif buyer_unavailable.is_set():
                log(f"[HIT - BUYER UNAVAILABLE] {now_ny()} {store} {title} :: "
                    f"buyer thread unavailable ({buyer_unavailable_reason}); no checkout attempted.")
            elif checkout_in_progress.is_set() or not signal_queue.empty():
                # An attempt is already running/pending; it covers all
                # priority stores in-browser. Never run two checkouts.
                log(f"[HIT DROPPED - CHECKOUT BUSY] {now_ny()} {store} {title} :: "
                    f"a checkout attempt is already in progress/pending; dropped, not queued.")
            else:
                try:
                    signal_queue.put_nowait({
                        "store": store, "title": title,
                        "available_stores": available, "detected_ny": now_ny(),
                    })
                    log(f"[HIT SIGNALLED IN-PROCESS] {now_ny()} {store} {title} :: "
                        f"available this poll: {available}; buyer wakes immediately (no DB/chat hop).")
                except queue.Full:
                    log(f"[HIT DROPPED - QUEUE FULL] {now_ny()} {store} {title}")
        # Small jitter so polls do not land on a metronome.
        time.sleep(max(1.0, POLL + random.uniform(0, 3)))


# ---------------------------------------------------------------------------
# Buyer thread: warm persistent context, wakes only on a hit signal
# ---------------------------------------------------------------------------

def _latch_hard_stop(reason: str) -> None:
    global hard_stop_reason
    if not hard_stop.is_set():
        hard_stop_reason = reason
        hard_stop.set()
        log(f"[BUYER HARD STOP] {reason} — no retry, no re-attempt on later hits "
            f"until operator restart after re-sign-in. Watcher keeps polling; "
            f"Duo Tracker remains the alert path.")


def run_checkout_attempt(page, hit: dict) -> str:
    """Run ONE checkout attempt on the buyer thread's warm page.

    Returns an outcome string for logging. Raises nothing to the caller
    for expected aborts: buyer.py's abort() (SystemExit) is caught here
    and converted into an attempt outcome, so a dry-run gate or an abort
    ends the ATTEMPT and returns the service to warm — it does NOT kill
    the merged process or stop the watcher (buyer.py standalone exits
    its process on these paths; the merged service must not, or one dry
    run would stop all watching).

    Buyer abort reasons are captured by temporarily wrapping
    buyer_driver.abort (buyer steps resolve `abort` from their module
    globals at call time). The wrapper records the reason, then calls
    the original, which logs 'NO ORDER PLACED' + reason and raises.
    """
    store = hit["store"]
    title = hit["title"]
    recorded = {"reason": ""}

    if buyer_driver is None:
        return "BUYER_DRIVER_MISSING"

    original_abort = buyer_driver.abort

    def recording_abort(reason: str, detail: str = ""):
        recorded["reason"] = f"{reason}" + (f" — {detail}" if detail else "")
        return original_abort(reason, detail)

    buyer_driver.abort = recording_abort  # type: ignore[attr-defined]
    submit_started = False
    try:
        buyer_driver.session_precheck(page)
        buyer_driver.select_configurator(page)
        buyer_driver.select_applecare_annual(page)
        buyer_driver.verify_bag(page)
        # select_pickup() already implements try-next-store in priority
        # order (Smith Haven > Walt Whitman > Roosevelt Field >
        # Manhasset, earliest slot each) and aborts STORE_SOLD_OUT only
        # if NO priority store has a slot. A single-store sellout
        # mid-checkout therefore moves to the next store inside this
        # call; it is not a failure of the whole attempt.
        pickup = buyer_driver.select_pickup(page)
        buyer_driver.select_default_payment(page)
        # Final re-verification immediately before the gate/submit.
        review = buyer_driver.verify_review(page, pickup)
        if not buyer_driver.LIVE_MODE:
            # DRY-RUN GATE (expected path in default mode): dumps review
            # text + screenshot, then raises SystemExit via abort().
            # Caught below and treated as a clean dry-run completion.
            try:
                buyer_driver.dry_run_gate(page, review)
            except SystemExit:
                log(f"[DRY-RUN COMPLETE] signal store {store} ({title}) :: "
                    f"review dumped to {DRY_RUN_REVIEW_TXT}, screenshot to "
                    f"{DRY_RUN_SCREENSHOT}. NO ORDER PLACED. Service returns "
                    f"to warm watching. NOTE: the bag still holds the dry-run "
                    f"items — clear the bag / restart before any live attempt.")
                send_result(store, title, False,
                            "Dry run complete — review dumped, Place Order NOT clicked.")
                return "DRY_RUN_COMPLETE"
            # dry_run_gate must never return normally; if it does, that
            # is a safety bug, not a success.
            log("[SAFETY BUG] dry_run_gate returned without aborting; treating as abort.")
            return "DRY_RUN_GATE_BUG"
        # Live path only: re-verify AGAIN immediately before submitting,
        # so nothing stale is submitted if the page changed.
        review = buyer_driver.verify_review(page, pickup)
        submit_started = True
        order_submitted.set()  # BEFORE the click: a retry can never double-submit
        buyer_driver.place_order_guarded(page, review)
        log("[LIVE SUBMIT] Place Order clicked ONCE. Operator must confirm "
            "the order confirmation page / order number in Apple order "
            "history separately; this log line is NOT proof of purchase.")
        send_result(store, title, True,
                    "Place Order clicked once — verify confirmation/order number in Apple order history; a push alone is not proof.")
        return "SUBMITTED_ONCE"
    except SystemExit:
        # Every buyer abort lands here (buyer already logged
        # 'NO ORDER PLACED' + reason). Classify for hard-stop latching.
        reason = recorded["reason"] or "UNKNOWN_ABORT"
        log(f"[CHECKOUT ABORTED] signal store {store} :: {reason}. NO ORDER PLACED.")
        if any(m in reason for m in HARD_STOP_MARKERS):
            _latch_hard_stop(reason)
            send_result(store, title, False,
                        f"Hard stop: {reason}. No retry. Operator must re-sign in and restart.")
        elif submit_started:
            # place_order_guarded raised after the latch was set: the
            # click may or may not have happened. Never retry.
            log("[AMBIGUOUS SUBMIT] Place Order was attempted but the step raised; "
                "outcome unknown — check Apple order history, DO NOT retry.")
            send_result(store, title, False,
                        "Place Order outcome AMBIGUOUS — check Apple order history; do not retry blind.")
        else:
            send_result(store, title, False, f"Aborted: {reason}. No order placed.")
        return f"ABORTED: {reason}"
    except Exception as e:
        # Playwright timeouts / page crashes / unexpected errors.
        if submit_started:
            log(f"[AMBIGUOUS SUBMIT] exception after submit started: {e!r} — "
                f"check Apple order history, DO NOT retry. NO RETRY WILL OCCUR.")
            send_result(store, title, False,
                        "Place Order outcome AMBIGUOUS after exception — check Apple order history; do not retry blind.")
            return "AMBIGUOUS_AFTER_SUBMIT"
        log(f"[CHECKOUT ERROR] signal store {store} :: {e!r}. NO ORDER PLACED.")
        send_result(store, title, False, f"Unexpected checkout error: {e!r}. No order placed.")
        return f"ERROR: {e!r}"
    finally:
        buyer_driver.abort = original_abort  # type: ignore[attr-defined]


def buyer_thread_main() -> None:
    """Owns the Playwright context for its whole lifetime. Playwright
    sync objects must only be touched from the thread that created
    them, so ALL browser work happens here — the watcher thread only
    ever touches the queue and the Event latches, never a page.

    Warm behaviour: launch ONE persistent context + ONE blank page and
    then block on queue.get(). No navigation to apple.com, no session
    refresh, no polling of Apple while warm. queue.get() returns the
    instant a hit is enqueued, so wake latency is ~1 s (queue wake +
    first navigation), not a poll interval."""
    global buyer_unavailable_reason
    if buyer_driver is None:
        buyer_unavailable_reason = f"buyer driver import failed: {_BUYER_IMPORT_ERROR!r}"
        buyer_unavailable.set()
        log(f"[buyer] UNAVAILABLE: {buyer_unavailable_reason}. Watcher keeps polling; no checkout will be attempted.")
        return
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        buyer_unavailable_reason = f"Playwright not installed/available: {e!r} (bake it into the image; see buyer/requirements.txt)"
        buyer_unavailable.set()
        log(f"[buyer] UNAVAILABLE: {buyer_unavailable_reason}. Watcher keeps polling; no checkout will be attempted.")
        return

    log(f"[buyer] Starting warm buyer. DRY_RUN={buyer_driver.DRY_RUN}, "
        f"LIVE_BUY_ENABLED={buyer_driver.LIVE_BUY_ENABLED}, "
        f"LIVE_MODE={buyer_driver.LIVE_MODE}, PROFILE_DIR={PROFILE_DIR}, HEADLESS={HEADLESS}.")
    if not buyer_driver.LIVE_MODE:
        log("[buyer] Dry-run mode: Place Order will NOT be clicked on any hit.")

    with sync_playwright() as p:
        context = None
        page = None
        try:
            # (a) Warm persistent context: reuses the user's own
            # signed-in session from PROFILE_DIR. No credentials are
            # handled here; an empty/expired profile simply fails the
            # session pre-check at checkout time with SESSION_EXPIRED.
            # Low-memory flags matter on a 1 CPU / 2 GB box: a single
            # context + single blank page idles cheaply (see design doc).
            context = p.chromium.launch_persistent_context(
                PROFILE_DIR,
                headless=HEADLESS,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--no-first-run",
                    "--disable-extensions",
                ],
            )
            page = context.new_page()
            log("[buyer] WARM READY: persistent browser warm, idle, waiting for an "
                "in-process watcher hit signal. No Apple requests made in warm mode. "
                "NO ORDER PLACED.")
        except Exception as e:
            buyer_unavailable_reason = f"could not launch warm browser context: {e!r}"
            buyer_unavailable.set()
            log(f"[buyer] UNAVAILABLE: {buyer_unavailable_reason}. Watcher keeps polling.")
            return

        while True:
            try:
                # Blocking get: wakes instantly on a hit; the timeout
                # only produces the periodic warm-alive log (same 300 s
                # cadence as the watcher heartbeat, not a poll of Apple).
                try:
                    hit = signal_queue.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    log(f"[buyer-warm] {now_ny()} still warm, idle, no Apple requests. "
                        f"buyer: {buyer_status()}")
                    continue
            except Exception as e:
                log(f"[buyer] signal wait error: {e!r}; continuing warm loop.")
                time.sleep(1)
                continue

            if order_submitted.is_set():
                log(f"[HIT SUPPRESSED - ALREADY SUBMITTED] {hit.get('store')} :: "
                    f"order already submitted this process lifetime; ignoring queued signal.")
                continue
            if hard_stop.is_set():
                log(f"[HIT SUPPRESSED - HARD STOP] {hit.get('store')} :: {hard_stop_reason}; ignoring queued signal.")
                continue

            # Ensure a usable page. If the page died, recreate it from
            # the same warm context; if the context died, relaunch it
            # once for this attempt (no tight relaunch loop — a second
            # failure aborts this attempt and the service stays warm).
            try:
                if page is None or page.is_closed():
                    page = context.new_page()
                    log("[buyer] Recreated page from warm context after previous page closed.")
            except Exception as e:
                log(f"[buyer] Page recreate failed ({e!r}); attempting one context relaunch.")
                try:
                    try:
                        context.close()
                    except Exception:
                        pass
                    context = p.chromium.launch_persistent_context(
                        PROFILE_DIR,
                        headless=HEADLESS,
                        args=["--disable-blink-features=AutomationControlled",
                              "--disable-dev-shm-usage", "--disable-gpu",
                              "--no-first-run", "--disable-extensions"],
                    )
                    page = context.new_page()
                except Exception as e2:
                    log(f"[buyer] Context relaunch failed ({e2!r}); aborting this attempt only. "
                        f"NO ORDER PLACED. Service stays warm; next hit will try relaunch again.")
                    send_result(hit.get("store", "?"), hit.get("title", ""),
                                False, f"Warm browser context lost and relaunch failed: {e2!r}. No order placed.")
                    continue

            checkout_in_progress.set()
            try:
                outcome = run_checkout_attempt(page, hit)
                log(f"[buyer] Checkout attempt outcome for {hit.get('store')}: {outcome}. "
                    f"Returning to warm. NO further action for this signal.")
            finally:
                checkout_in_progress.clear()
                # The page is intentionally reused (same context/session)
                # for the next attempt; buyer steps navigate it as needed.
                # If the page died mid-attempt, the next hit's ensure-page
                # block above recreates it.


def main() -> None:
    """Entry point. Two modes, and they are NOT interchangeable:
    * Default (no args): full merged service — watcher thread on the
      main thread + warm buyer thread. This is the production mode
      the Dockerfile CMD uses.
    * ``--warm``: WARM-ONLY test/idle mode — buyer thread only, NO
      watcher loop, hence NO Apple pickup requests at all, NO
      checkout, and NO hit can ever arrive (the only hit producer
      is the watcher, which is not running). The buyer launches its
      persistent context + blank page and blocks on the queue
      forever. Use it to validate a warm session / browser launch
      without polling Apple. NEVER use it as the production startup
      mode: a --warm service watches nothing.
    Any other argv is logged and ignored (never a checkout trigger).
    """
    unknown = [a for a in sys.argv[1:] if a != "--warm"]
    if unknown:
        log(f"[merged] Ignoring unknown argv (no effect on safety gates): {unknown}")
    log("[merged] Starting merged watcher+buyer service (one process). "
        f"DRY_RUN default true / LIVE_BUY_ENABLED default false; "
        f"buyer LIVE_MODE={(buyer_driver.LIVE_MODE if buyer_driver else 'UNKNOWN (driver missing)')}.")
    buyer_thread = threading.Thread(target=buyer_thread_main, name="buyer", daemon=True)
    buyer_thread.start()
    if "--warm" in sys.argv[1:]:
        log("[merged] --warm mode: buyer warm/idle ONLY. Watcher NOT "
            "running, so NO Apple pickup requests, NO hit signals, "
            "NO checkout. This is a warm-up/session-validation mode, "
            "not production. NO ORDER PLACED.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            log("[merged] Interrupted (warm mode). NO ORDER PLACED.")
            sys.exit(0)
    # Watcher runs on the main thread so KeyboardInterrupt / process
    # exit cleanly stops both (buyer thread is a daemon).
    try:
        watcher_loop()
    except KeyboardInterrupt:
        log("[merged] Interrupted. NO ORDER PLACED by this shutdown.")
        sys.exit(0)


if __name__ == "__main__":
    main()
