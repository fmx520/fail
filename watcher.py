#!/usr/bin/env python3
"""
iPhone Duo in-store pickup watcher.

Reads Apple's public pickup-message endpoint (the same one apple.com uses
for "Check availability" next to Pickup), filters to the four Long Island
stores, and sends ONE push per store+config transition (unavailable -> available).

Design notes, all deliberate:
- ONE request per poll, with every watched part in it (parts.0, parts.1, ...).
  Polling "one request per config" multiplies requests and is the fastest way
  to get rate-limited by Apple.
- A failed / blocked / unparseable check is NOT "sold out". It is logged as
  a failed check and the previous state is kept, so a 429/541 never closes
  an open drop or invents one.
- A hit requires BOTH: pickupDisplay != "unavailable" AND
  messageTypes.regular.storeSelectionEnabled == True, using Apple's own
  storePickupProductTitle for the config name. Never trust a part number
  from a third-party list until Apple returns that exact title for it.
- Drop log is JSONL by default (drop_log.jsonl next to this file).
  If DATABASE_URL is set and psycopg is importable, transitions are also
  written to the `drops` table in schema.sql.

Environment:
  APPLE_LOCATION        default "11754"
  PARTS                 comma list; Parker primary (2026-10-30):
                        "MK1H4LL/A" = iPhone Duo 512GB Night Sky, $2,199
  TARGET_STORES         comma list of Apple storeName values
  POLL_INTERVAL_SECONDS default 12 (spec). See README: Apple has blocked
                        watchers polling once a minute; backoff below is
                        the safety net, not a guarantee.
  PUSH_PROVIDER         "pushover" (default) or "ntfy"
  PUSHOVER_TOKEN        Pushover app token (secret, never commit)
  PUSHOVER_USER         Pushover user key (secret, never commit)
  NTFY_TOPIC            e.g. "https://ntfy.sh/iphone-duo-xxxx" (secret topic)
  BUY_URL               default "https://www.apple.com/shop/buy-iphone/iphone-duo"
  DROP_LOG              default "drop_log.jsonl"
"""
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
APPLE_URL = "https://www.apple.com/shop/retail/pickup-message"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 27_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/27.0 Mobile/15E148 Safari/604.1")

LOCATION = os.environ.get("APPLE_LOCATION", "11754")
PARTS = [p.strip() for p in os.environ.get(
    "PARTS", "MK1H4LL/A").split(",") if p.strip()]
TARGET_STORES = [s.strip() for s in os.environ.get(
    "TARGET_STORES", "Smith Haven,Walt Whitman,Roosevelt Field,Manhasset").split(",") if s.strip()]
POLL = float(os.environ.get("POLL_INTERVAL_SECONDS", "12"))
BUY_URL = os.environ.get("BUY_URL", "https://www.apple.com/shop/buy-iphone/iphone-duo")
DROP_LOG = os.environ.get("DROP_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "drop_log.jsonl"))
PUSH_PROVIDER = os.environ.get("PUSH_PROVIDER", "pushover").lower()
# Scope decided 2026-10-30: the existing Vercel Duo Tracker handles
# availability alerts. This Render worker is for AUTO-CHECKOUT ONLY.
# CHECKOUT_ONLY=true means: on a hit, do NOT send a duplicate availability
# alert. Either attempt checkout (if AUTO_CHECKOUT_ENABLED=true and the
# checkout driver in checkout.py is configured) and push the RESULT
# (bought / failed), or just log the hit and let the existing app alert.
CHECKOUT_ONLY = os.environ.get("CHECKOUT_ONLY", "true").lower() == "true"
AUTO_CHECKOUT_ENABLED = os.environ.get("AUTO_CHECKOUT_ENABLED", "false").lower() == "true"


def now_ny():
    return datetime.now(NY).isoformat(timespec="seconds")


def fetch_availability():
    params = [("pl", "true"), ("mts.0", "regular"), ("location", LOCATION)]
    for i, part in enumerate(PARTS):
        params.append((f"parts.{i}", part))
    url = APPLE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def is_hit(avail):
    """Return True only on Apple's own selectable-availability signals."""
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


def send_push(store, title, quote):
    detected = now_ny()
    message = f"{title} available for pickup at Apple {store} — {detected} ET. Tap to open the iPhone Duo page."
    if PUSH_PROVIDER == "ntfy":
        topic = os.environ["NTFY_TOPIC"]
        req = urllib.request.Request(
            topic, data=message.encode(),
            headers={"Title": f"iPhone Duo: {store}", "Priority": "max",
                     "Tags": "iphone,apple", "Click": BUY_URL},
            method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    else:  # pushover, priority 2 = emergency: retries until acknowledged
        data = urllib.parse.urlencode({
            "token": os.environ["PUSHOVER_TOKEN"],
            "user": os.environ["PUSHOVER_USER"],
            "title": f"iPhone Duo available — {store}",
            "message": message,
            "priority": "2", "retry": "30", "expire": "600",
            "url": BUY_URL, "url_title": "Open iPhone Duo in Apple Store",
            "sound": "siren",
        }).encode()
        req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    print(f"[ALERT] {detected} {store} {title} :: {quote}", flush=True)


def send_result_push(store, title, success, detail):
    """Checkout result only: bought, or failed with the reason. No
    availability alert here — the existing Duo Tracker app handles that."""
    detected = now_ny()
    verdict = "BOUGHT" if success else "CHECKOUT FAILED"
    message = (f"{verdict}: {title} at Apple {store} — {detected} ET. {detail} "
               f"Check your Apple order status to confirm; do not assume a "
               f"push alone means an order exists.")
    if PUSH_PROVIDER == "ntfy":
        topic = os.environ["NTFY_TOPIC"]
        req = urllib.request.Request(
            topic, data=message.encode(),
            headers={"Title": f"iPhone Duo checkout: {store}", "Priority": "max",
                     "Tags": "iphone,apple", "Click": BUY_URL},
            method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    else:
        data = urllib.parse.urlencode({
            "token": os.environ["PUSHOVER_TOKEN"],
            "user": os.environ["PUSHOVER_USER"],
            "title": f"iPhone Duo checkout — {store}",
            "message": message,
            "priority": "2", "retry": "30", "expire": "600",
            "url": BUY_URL, "url_title": "Check Apple order status",
            "sound": "siren",
        }).encode()
        req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    print(f"[CHECKOUT RESULT] {detected} {store} {title} success={success} :: {detail}", flush=True)


def log_transition(store, part, title, appeared=None, disappeared=None):
    rec = {"store": store, "part": part, "config": title,
           "appeared_at_ny": appeared, "disappeared_at_ny": disappeared}
    with open(DROP_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
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
            print(f"[DB WARN] {e}", flush=True)


HEARTBEAT_SECONDS = float(os.environ.get("HEARTBEAT_SECONDS", "300"))


def main():
    state = {}   # (store, part) -> (hit, title, appeared_at_ny)
    backoff = POLL
    last_heartbeat = time.monotonic()
    print(f"Watching {PARTS} at {TARGET_STORES} from {LOCATION}, poll={POLL}s", flush=True)
    while True:
        try:
            current = parse(fetch_availability())
        except urllib.error.HTTPError as e:
            # 429 / 541 (Apple's block page) / 503: back off, keep state.
            print(f"[CHECK FAILED] HTTP {e.code} at {now_ny()} — backing off to {min(backoff * 2, 300):.0f}s", flush=True)
            backoff = min(backoff * 2, 300)
            time.sleep(backoff)
            continue
        except Exception as e:
            print(f"[CHECK FAILED] {e} at {now_ny()} — backing off", flush=True)
            backoff = min(backoff * 2, 300)
            time.sleep(backoff)
            continue
        backoff = POLL  # a good check clears the backoff
        if time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
            hits = sum(1 for v in current.values() if v[0])
            print(f"[HEARTBEAT] {now_ny()} last Apple check OK, "
                  f"{hits}/{len(current)} target store/configs available", flush=True)
            last_heartbeat = time.monotonic()
        for key, (hit, title, quote) in current.items():
            store, part = key
            prev_hit, _, appeared = state.get(key, (False, title, None))
            if hit and not prev_hit:
                if AUTO_CHECKOUT_ENABLED:
                    from checkout import attempt_checkout
                    success, detail = attempt_checkout(store, part, title)
                    send_result_push(store, title, success, detail)
                elif CHECKOUT_ONLY:
                    print(f"[HIT - ALERT SUPPRESSED] {now_ny()} {store} {title} :: "
                          f"checkout not enabled; existing Duo Tracker app handles alerts", flush=True)
                else:
                    send_push(store, title, quote)
                state[key] = (True, title, now_ny())
            elif not hit and prev_hit:
                log_transition(store, part, title, appeared=appeared, disappeared=now_ny())
                print(f"[SOLD OUT] {now_ny()} {store} {title} (was up since {appeared})", flush=True)
                state[key] = (False, title, None)
            else:
                state[key] = (hit, title, appeared)
        # Small jitter so polls do not land on a metronome.
        time.sleep(max(1.0, POLL + random.uniform(0, 3)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
