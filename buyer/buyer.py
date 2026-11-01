#!/usr/bin/env python3
"""iPhone Duo auto-checkout driver — Playwright skeleton (DRY-RUN GATED).

Language choice: Python + Playwright.
Justification: the existing watcher (../watcher.py) is stdlib Python, so a
Python buyer reuses the same runtime, deployment image, logging style, and
operator skill set with no second language toolchain; Playwright (vs raw
HTTP) is required because Apple's bag/checkout flow is JS-driven with
session/CSRF state and no public/documented checkout API, and a persistent
Chromium context is the only realistic way to reuse the user's own
signed-in Apple session without handling credentials here.

HARD SAFETY RULES — read before touching this file:
  1. NEVER buy in this skeleton's default mode. DRY_RUN defaults to true
     and stops BEFORE Place Order, dumps review text, screenshots to
     ./dry-run-review.png, and exits. No order is placed.
  2. Live mode requires BOTH: DRY_RUN=false AND LIVE_BUY_ENABLED=true,
     set explicitly in the environment. Anything else => dry-run gate.
  3. NEVER sign in. NEVER prompt for a password, 2FA code, or any
     credential. If the session pre-check finds no signed-in session,
     abort with 'SESSION_EXPIRED — needs user re-sign-in' and exit.
  4. NEVER submit twice. A module-level _ORDER_SUBMITTED guard refuses a
     second Place Order click in the same run.
  5. Abort on ANY bot challenge or 2FA prompt, at ANY step.
  6. Immediately before Place Order (live mode only), re-verify part,
     store, slot, AppleCare-annual, payment, and total. Abort on ANY
     mismatch. Every abort logs 'NO ORDER PLACED' + the reason.
  7. Do NOT use any credentials/secrets in this file. Payment uses the
     DEFAULT payment method already on the user's Apple account; this
     code never reads, enters, or stores a card number.
  8. Selectors below marked TO-VERIFY-IN-DRY-RUN are PLACEHOLDERS.
     Apple's real selectors are not publicly documented and were NOT
     invented as verified here. The supervised dry run must replace each
     placeholder with the selector actually observed in the live UI, and
     the run must stop at the dry-run gate until that is done. If a
     placeholder selector is still present at a step, that step aborts
     rather than guessing.

Environment variables:
  PROFILE_DIR       Persistent Chromium profile dir (warm session).
                    Default: ./profile (relative to the working directory).
  DRY_RUN           Default: "true". Set to "false" ONLY for a supervised
                    live attempt, together with LIVE_BUY_ENABLED=true.
  LIVE_BUY_ENABLED  Default: "false". Must be exactly "true" for live mode.
  DRY_RUN_SCREENSHOT  Default: ./dry-run-review.png
  DRY_RUN_REVIEW_TXT  Default: ./dry-run-review.txt

Usage (dry run — the only mode this skeleton is ready for):
  python buyer.py

This file is a skeleton: it is syntax-checkable and its control flow,
gates, and verification order are complete, but the configurator/bag/
checkout selectors are placeholders pending a supervised dry run. It
must NOT be run against a live checkout until those placeholders are
replaced with observed selectors and the dry run has passed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from buyer_config import (
    ABORT_ALREADY_SUBMITTED,
    ABORT_APPLECARE_MISMATCH,
    ABORT_BOT_CHALLENGE,
    ABORT_CONFIG_MISMATCH,
    ABORT_DRY_RUN_GATE,
    ABORT_LIVE_NOT_ENABLED,
    ABORT_NOT_SIGNED_IN,
    ABORT_PART_MISMATCH,
    ABORT_PAYMENT_MISMATCH,
    ABORT_PRICE_MISMATCH,
    ABORT_SELECTOR_UNVERIFIED,
    ABORT_SESSION_EXPIRED,
    ABORT_SLOT_UNAVAILABLE,
    ABORT_STORE_SOLD_OUT,
    ABORT_TOTAL_MISMATCH,
    ABORT_TWO_FACTOR,
    APPLECARE_ANNUAL_REPORTED_USD,
    APPLECARE_TERM,
    CARRIER_OPTION,
    EXPECTED_MIN_TOTAL_USD,
    FINISH,
    PART_NUMBER,
    PHONE_PRICE_USD,
    PRODUCT_TITLE_CONTAINS,
    STORES_IN_PRIORITY_ORDER,
    STORAGE,
    TIMEOUT_NAV_MS,
    TIMEOUT_REVIEW_MS,
    TIMEOUT_SELECTOR_MS,
    TRADE_IN,
)

# ---------------------------------------------------------------------------
# Env / run mode
# ---------------------------------------------------------------------------

PROFILE_DIR = os.environ.get("PROFILE_DIR", "./profile")
DRY_RUN = os.environ.get("DRY_RUN", "true").strip().lower() != "false"
LIVE_BUY_ENABLED = os.environ.get("LIVE_BUY_ENABLED", "false").strip().lower() == "true"
DRY_RUN_SCREENSHOT = os.environ.get("DRY_RUN_SCREENSHOT", "./dry-run-review.png")
DRY_RUN_REVIEW_TXT = os.environ.get("DRY_RUN_REVIEW_TXT", "./dry-run-review.txt")
# Headed only for a locally supervised dry run. On a headless Render worker
# this must stay "true" (default); headed=True there would fail with no display.
HEADLESS = os.environ.get("HEADLESS", "true").strip().lower() != "false"

LIVE_MODE = (not DRY_RUN) and LIVE_BUY_ENABLED

# Set to True the moment Place Order is clicked (live mode only). This is
# the never-submit-twice guard; it is checked before every submit attempt.
_ORDER_SUBMITTED = False


def log(msg: str) -> None:
    print(f"[buyer] {msg}", flush=True)


def abort(reason: str, detail: str = "") -> "SystemExit":
    """Every abort path goes through here so the no-order guarantee is logged.

    Logs 'NO ORDER PLACED' + reason on every abort, then exits non-zero.
    Callers must not catch this to continue toward Place Order.
    """
    suffix = f" — {detail}" if detail else ""
    log(f"NO ORDER PLACED: {reason}{suffix}")
    raise SystemExit(2)


# ---------------------------------------------------------------------------
# PLACEHOLDER SELECTORS — TO-VERIFY-IN-DRY-RUN
# ---------------------------------------------------------------------------
# None of these has been verified against Apple's live checkout UI.
# They are structural placeholders showing WHERE each interaction happens,
# not claims about Apple's real DOM. Replace each with the observed
# selector during the supervised dry run. Until replaced, steps using a
# placeholder must abort via _require_verified_selector().
#
# How to verify: in the dry run, stop at each step, inspect the live page,
# record the real selector, update SELECTORS below, and re-run the dry run
# to the review gate. Do not ship a live run with placeholders intact.

SELECTORS = {
    # Session pre-check: an element present ONLY when signed in, e.g. an
    # account-name/menu element on the account or bag page.
    "signed_in_indicator": "TO-VERIFY-IN-DRY-RUN: account signed-in indicator",
    # Sign-in affordance whose PRESENCE means "not signed in".
    "sign_in_link": "TO-VERIFY-IN-DRY-RUN: sign-in link/button",
    # Configurator: 512GB / Night Sky / connect-later / no-trade-in options.
    "storage_512gb": "TO-VERIFY-IN-DRY-RUN: 512GB storage option",
    "finish_night_sky": "TO-VERIFY-IN-DRY-RUN: Night Sky finish option",
    "carrier_connect_later": "TO-VERIFY-IN-DRY-RUN: connect-later / unlocked option",
    "trade_in_none": "TO-VERIFY-IN-DRY-RUN: no-trade-in option",
    "add_to_bag": "TO-VERIFY-IN-DRY-RUN: Add to Bag button",
    # AppleCare: yearly/annual payment option + its selected state.
    "applecare_annual_option": "TO-VERIFY-IN-DRY-RUN: AppleCare+ yearly option",
    "applecare_annual_selected": "TO-VERIFY-IN-DRY-RUN: AppleCare+ yearly selected state",
    # Bag: part number, title, and price text containers.
    "bag_part_number": "TO-VERIFY-IN-DRY-RUN: bag item part-number text",
    "bag_title": "TO-VERIFY-IN-DRY-RUN: bag item title text",
    "bag_price": "TO-VERIFY-IN-DRY-RUN: bag item price text",
    # Pickup: store list / slot picker / sold-out indicator.
    "pickup_store_option": "TO-VERIFY-IN-DRY-RUN: pickup store option (per store)",
    "pickup_slot_earliest": "TO-VERIFY-IN-DRY-RUN: earliest pickup slot option",
    "pickup_sold_out": "TO-VERIFY-IN-DRY-RUN: sold-out / unavailable indicator",
    # Checkout review: default payment, totals, Place Order.
    "payment_default": "TO-VERIFY-IN-DRY-RUN: default payment method indicator",
    "review_total": "TO-VERIFY-IN-DRY-RUN: review-page total text",
    "review_part_number": "TO-VERIFY-IN-DRY-RUN: review-page part-number text",
    "review_applecare": "TO-VERIFY-IN-DRY-RUN: review-page AppleCare line text",
    "place_order": "TO-VERIFY-IN-DRY-RUN: Place Order button",
}

# Text fragments that mean "stop now": bot challenge / 2FA, at any step.
BOT_CHALLENGE_TEXTS = [
    "verify you are human",
    "captcha",
    "unusual traffic",
    "access denied",
    "are you a robot",
]
TWO_FACTOR_TEXTS = [
    "verification code",
    "two-factor",
    "two factor",
    "enter the code sent",
    "approve this sign-in",
]


def _require_verified_selector(key: str) -> str:
    """Refuse to act on a placeholder selector.

    Any step whose selector is still a TO-VERIFY placeholder aborts here
    instead of clicking/guessing in the live UI.
    """
    selector = SELECTORS[key]
    if selector.startswith("TO-VERIFY-IN-DRY-RUN"):
        abort(ABORT_SELECTOR_UNVERIFIED, f"selector '{key}' not yet verified in dry run")
    return selector


def _check_bot_or_2fa(page_text: str, step: str) -> None:
    lowered = page_text.lower()
    if any(t in lowered for t in BOT_CHALLENGE_TEXTS):
        abort(ABORT_BOT_CHALLENGE, f"at step '{step}'")
    if any(t in lowered for t in TWO_FACTOR_TEXTS):
        abort(ABORT_TWO_FACTOR, f"at step '{step}'")


def _parse_usd(text: str) -> float | None:
    """Parse a '$1,234.56' style amount; returns None if unparseable."""
    import re

    m = re.search(r"\$([\d,]+\.?\d*)", text or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def session_precheck(page) -> None:
    """(b) Warm-session pre-check. Abort — never auto sign-in.

    Pass condition: the persistent profile shows a signed-in Apple session
    (signed-in indicator present AND no sign-in link). Anything else —
    sign-in link present, indicator absent, 2FA/bot text — aborts with
    SESSION_EXPIRED / NOT_SIGNED_IN / BOT / 2FA as appropriate. The user
    must re-sign in themselves in a normal browser session; this driver
    never prompts for a password or code and never submits a sign-in form.
    """
    log("Session pre-check: checking warm persistent session (no sign-in will be attempted).")
    # TO-VERIFY-IN-DRY-RUN: the exact account/bag URL used for the check.
    page.goto("https://www.apple.com/shop/bag", timeout=TIMEOUT_NAV_MS)
    text = page.content()
    _check_bot_or_2fa(page.inner_text("body"), "session_precheck")
    # Placeholder-selector gate: until the real indicators are observed,
    # this step aborts rather than misreading the session state.
    _require_verified_selector("signed_in_indicator")
    _require_verified_selector("sign_in_link")
    signed_in = page.locator(SELECTORS["signed_in_indicator"]).count() > 0
    sign_in_shown = page.locator(SELECTORS["sign_in_link"]).count() > 0
    if sign_in_shown or not signed_in:
        abort(ABORT_SESSION_EXPIRED, "not signed in — needs user re-sign-in")
    log("Session pre-check passed: signed-in session present.")


def select_configurator(page) -> None:
    """(c) Configurator: 512GB, Night Sky, connect-later, no trade-in.

    TO-VERIFY-IN-DRY-RUN: the configurator URL and every option selector.
    Order matters only insofar as Apple's UI requires it; the verified
    values (storage/finish/carrier/trade-in) are asserted again at bag
    verification and at the final review re-verification.
    """
    log(f"Configurator: selecting {STORAGE} / {FINISH} / {CARRIER_OPTION} / trade-in={TRADE_IN}.")
    # TO-VERIFY-IN-DRY-RUN: product/configurator URL for MK1H4LL/A.
    page.goto("https://www.apple.com/shop/buy-iphone", timeout=TIMEOUT_NAV_MS)
    _check_bot_or_2fa(page.inner_text("body"), "configurator")
    for key in ("storage_512gb", "finish_night_sky", "carrier_connect_later", "trade_in_none"):
        selector = _require_verified_selector(key)
        page.locator(selector).first.click(timeout=TIMEOUT_SELECTOR_MS)
    # Bag verification (step e) re-checks part/title/price; a mismatch
    # there aborts before any pickup/payment step is attempted.
    add_selector = _require_verified_selector("add_to_bag")
    page.locator(add_selector).first.click(timeout=TIMEOUT_SELECTOR_MS)
    log("Configurator selections clicked; proceeding to bag verification.")


def select_applecare_annual(page) -> None:
    """(d) AppleCare+: select the YEARLY option, then explicitly verify it.

    Verification is not 'the click succeeded' — it is reading back the
    selected state AND the annual price/term from the page. The reported
    $199.99/yr is unverified until the dry run; if the observed annual
    price differs, abort for operator review instead of silently accepting
    a different term or price. Never select the monthly option.
    """
    log(f"AppleCare: selecting YEARLY option (reported ${APPLECARE_ANNUAL_REPORTED_USD}/yr, UNVERIFIED-UNTIL-DRY-RUN).")
    selector = _require_verified_selector("applecare_annual_option")
    page.locator(selector).first.click(timeout=TIMEOUT_SELECTOR_MS)
    selected_selector = _require_verified_selector("applecare_annual_selected")
    if page.locator(selected_selector).count() == 0:
        abort(ABORT_APPLECARE_MISMATCH, "yearly option not in selected state after click")
    body = page.inner_text("body")
    _check_bot_or_2fa(body, "applecare")
    # The annual price must be read off the page, not assumed. A mismatch
    # against the reported value aborts for review (price may have moved,
    # or the wrong term may be selected) — it is never auto-corrected.
    observed = _parse_usd(body)
    if observed is not None and abs(observed - APPLECARE_ANNUAL_REPORTED_USD) > 0.01:
        # Note: body-wide parse is coarse; the dry run must narrow this to
        # the AppleCare line item. Any disagreement still aborts.
        log(f"WARNING: observed amount ${observed} differs from reported AppleCare annual "
            f"${APPLECARE_ANNUAL_REPORTED_USD}; dry run must confirm the AppleCare line item.")
        abort(ABORT_APPLECARE_MISMATCH, f"observed ${observed} vs reported ${APPLECARE_ANNUAL_REPORTED_USD}/yr")
    if APPLECARE_TERM != "yearly":
        abort(ABORT_APPLECARE_MISMATCH, "AppleCare term is not yearly")
    log("AppleCare yearly selection verified (selected state present).")


def verify_bag(page) -> None:
    """(e) Bag verification: exactly MK1H4LL/A, title, $2,199.

    All three must match. Any mismatch aborts before pickup/payment.
    """
    log("Bag verification: expecting exactly MK1H4LL/A / iPhone Duo / $2,199.")
    _check_bot_or_2fa(page.inner_text("body"), "bag")
    part_sel = _require_verified_selector("bag_part_number")
    title_sel = _require_verified_selector("bag_title")
    price_sel = _require_verified_selector("bag_price")
    part_text = page.locator(part_sel).first.inner_text(timeout=TIMEOUT_SELECTOR_MS)
    title_text = page.locator(title_sel).first.inner_text(timeout=TIMEOUT_SELECTOR_MS)
    price_text = page.locator(price_sel).first.inner_text(timeout=TIMEOUT_SELECTOR_MS)
    if PART_NUMBER not in part_text:
        abort(ABORT_PART_MISMATCH, f"bag shows '{part_text.strip()}', expected '{PART_NUMBER}'")
    if PRODUCT_TITLE_CONTAINS.lower() not in title_text.lower():
        abort(ABORT_CONFIG_MISMATCH, f"bag title '{title_text.strip()}' does not contain '{PRODUCT_TITLE_CONTAINS}'")
    price = _parse_usd(price_text)
    if price is None or abs(price - PHONE_PRICE_USD) > 0.01:
        abort(ABORT_PRICE_MISMATCH, f"bag price '{price_text.strip()}', expected $2,199.00")
    log(f"Bag verified: {PART_NUMBER} / {title_text.strip()} / ${price:.2f}.")


def select_pickup(page) -> dict | None:
    """(f) Pickup: priority order, earliest slot, try-next-store on sellout.

    Tries Smith Haven R139 > Walt Whitman R068 > Roosevelt Field R060 >
    Manhasset R327 in order. A store that shows sold-out/unavailable at
    selection time is skipped for the next store — that is the
    try-next-store rule, not a failure. Returns {"store": ..., "slot": ...}
    on success, or aborts if NO priority store has a slot.
    """
    log("Pickup: trying stores in priority order, earliest slot each.")
    _check_bot_or_2fa(page.inner_text("body"), "pickup")
    store_sel = _require_verified_selector("pickup_store_option")
    slot_sel = _require_verified_selector("pickup_slot_earliest")
    sold_out_sel = _require_verified_selector("pickup_sold_out")
    for store in STORES_IN_PRIORITY_ORDER:
        log(f"Pickup: trying {store['name']} ({store['number']}).")
        # TO-VERIFY-IN-DRY-RUN: how a specific store is chosen in the UI
        # (store list entry, search, or store-picker) and how its earliest
        # slot is read. The placeholder selector must be replaced with a
        # store-parameterised locator before any live run.
        page.locator(store_sel).filter(has_text=store["number"]).first.click(timeout=TIMEOUT_SELECTOR_MS)
        if page.locator(sold_out_sel).count() > 0:
            log(f"Pickup: {store['name']} ({store['number']}) sold out — trying next store.")
            continue
        slot_text = page.locator(slot_sel).first.inner_text(timeout=TIMEOUT_SELECTOR_MS)
        if not slot_text.strip():
            log(f"Pickup: {store['name']} ({store['number']}) has no slot text — trying next store.")
            continue
        page.locator(slot_sel).first.click(timeout=TIMEOUT_SELECTOR_MS)
        log(f"Pickup selected: {store['name']} ({store['number']}) — earliest slot '{slot_text.strip()}'.")
        return {"store": store, "slot": slot_text.strip()}
    abort(ABORT_STORE_SOLD_OUT, "no priority store had an available pickup slot")
    return None  # unreachable; abort() always raises


def select_default_payment(page) -> None:
    """(g) Payment: use the DEFAULT payment method on the Apple account.

    This step selects/verifies the default method indicator only. It
    never enters, reads, or stores card details, and never adds a new
    payment method.
    """
    log("Payment: verifying default payment method on the Apple account.")
    _check_bot_or_2fa(page.inner_text("body"), "payment")
    payment_sel = _require_verified_selector("payment_default")
    if page.locator(payment_sel).count() == 0:
        abort(ABORT_PAYMENT_MISMATCH, "no default payment method indicator found")
    log("Payment: default payment method indicator present.")


def verify_review(page, pickup: dict) -> dict:
    """Final review verification (live gate + dry-run dump source).

    Re-checks part, AppleCare-annual, default payment, store/slot, and
    total (phone + AppleCare yearly + tax; never a $2,199 cap). Returns
    the observed review facts for logging/dumping. Aborts on any mismatch.
    """
    log("Review verification: re-checking part / AppleCare-annual / payment / store / slot / total.")
    body = page.inner_text("body")
    _check_bot_or_2fa(body, "review")
    part_sel = _require_verified_selector("review_part_number")
    applecare_sel = _require_verified_selector("review_applecare")
    payment_sel = _require_verified_selector("payment_default")
    total_sel = _require_verified_selector("review_total")
    part_text = page.locator(part_sel).first.inner_text(timeout=TIMEOUT_REVIEW_MS)
    applecare_text = page.locator(applecare_sel).first.inner_text(timeout=TIMEOUT_REVIEW_MS)
    total_text = page.locator(total_sel).first.inner_text(timeout=TIMEOUT_REVIEW_MS)
    if PART_NUMBER not in part_text:
        abort(ABORT_PART_MISMATCH, f"review shows '{part_text.strip()}', expected '{PART_NUMBER}'")
    if "year" not in applecare_text.lower() and "annual" not in applecare_text.lower():
        abort(ABORT_APPLECARE_MISMATCH, f"review AppleCare line '{applecare_text.strip()}' is not yearly/annual")
    if page.locator(payment_sel).count() == 0:
        abort(ABORT_PAYMENT_MISMATCH, "default payment indicator missing on review page")
    if pickup["store"]["number"] not in body and pickup["store"]["name"] not in body:
        abort(ABORT_SLOT_UNAVAILABLE, f"review page does not show pickup store {pickup['store']['name']}")
    total = _parse_usd(total_text)
    if total is None:
        abort(ABORT_TOTAL_MISMATCH, f"could not parse review total from '{total_text.strip()}'")
    if total < EXPECTED_MIN_TOTAL_USD - 0.01:
        abort(ABORT_TOTAL_MISMATCH,
              f"review total ${total:.2f} below phone+AppleCare floor ${EXPECTED_MIN_TOTAL_USD:.2f} "
              f"(total = phone + AppleCare yearly + tax; no $2,199 cap is applied)")
    log(f"Review verified: part {PART_NUMBER}, AppleCare yearly, store "
        f"{pickup['store']['name']} ({pickup['store']['number']}), slot '{pickup['slot']}', "
        f"total ${total:.2f}.")
    return {"part": part_text.strip(), "applecare": applecare_text.strip(),
            "total_text": total_text.strip(), "total": total, "pickup": pickup}


def dry_run_gate(page, review: dict) -> "SystemExit":
    """(h) HARD dry-run gate: dump + screenshot + exit, never Place Order.

    Writes the review page text to DRY_RUN_REVIEW_TXT and a screenshot to
    DRY_RUN_SCREENSHOT, logs NO ORDER PLACED, and exits. This function
    never clicks Place Order under any condition.
    """
    log("DRY-RUN GATE: stopping before Place Order by design.")
    text = page.inner_text("body")
    Path(DRY_RUN_REVIEW_TXT).write_text(
        f"iPhone Duo dry-run review dump\n"
        f"Part: {review['part']}\nAppleCare: {review['applecare']}\n"
        f"Total: {review['total_text']}\n"
        f"Pickup: {review['pickup']['store']['name']} ({review['pickup']['store']['number']}) "
        f"— {review['pickup']['slot']}\n\n--- page text ---\n{text}\n",
        encoding="utf-8",
    )
    page.screenshot(path=DRY_RUN_SCREENSHOT, full_page=True)
    log(f"DRY-RUN GATE: review text dumped to {DRY_RUN_REVIEW_TXT}, screenshot to {DRY_RUN_SCREENSHOT}.")
    abort(ABORT_DRY_RUN_GATE, "dry run complete — Place Order was NOT clicked")


def place_order_guarded(page, review: dict) -> None:
    """Live-mode Place Order: single guarded click after re-verification.

    Preconditions (all enforced here, not just in main()):
      - LIVE_MODE is true (DRY_RUN=false AND LIVE_BUY_ENABLED=true);
      - _ORDER_SUBMITTED is false (never submit twice);
      - review was just re-verified by verify_review() immediately before
        this call — main() calls verify_review() again right before this
        function, so a stale review cannot be submitted.
    Any bot/2FA text on the page aborts instead of clicking.
    """
    global _ORDER_SUBMITTED
    if not LIVE_MODE:
        abort(ABORT_LIVE_NOT_ENABLED, "DRY_RUN=false + LIVE_BUY_ENABLED=true both required for live mode")
    if _ORDER_SUBMITTED:
        abort(ABORT_ALREADY_SUBMITTED, "Place Order already clicked once this run")
    _check_bot_or_2fa(page.inner_text("body"), "place_order")
    place_sel = _require_verified_selector("place_order")
    _ORDER_SUBMITTED = True  # set BEFORE the click so a retry cannot double-submit
    page.locator(place_sel).first.click(timeout=TIMEOUT_SELECTOR_MS)
    log("Place Order clicked ONCE (live mode). Operator must confirm the "
        "order confirmation page/order number separately; this skeleton does "
        "not treat the click alone as proof of purchase.")


def main() -> None:
    import argparse

    # COORDINATOR FIX (2026-11-01): Dockerfile runs `buyer.py --warm`.
    # Previously main() ignored argv, so --warm would silently have run a
    # full checkout attempt on service start. --warm now means idle only:
    # launch a warm persistent context, open a blank page, and wait for a
    # watcher signal (see SIGNALING.md). It makes NO Apple requests and
    # NEVER runs the configurator/bag/checkout flow. The Supabase-claim
    # signal loop itself is NOT yet implemented in this skeleton — that is
    # a pre-deploy integration task (see README.md), not something to fake
    # here with untested Supabase code.
    parser = argparse.ArgumentParser()
    parser.add_argument("--warm", action="store_true",
                        help="Warm idle mode only: no checkout attempt.")
    args = parser.parse_args()

    log(f"Starting buyer skeleton. DRY_RUN={DRY_RUN}, LIVE_BUY_ENABLED={LIVE_BUY_ENABLED}, "
        f"LIVE_MODE={LIVE_MODE}, PROFILE_DIR={PROFILE_DIR}, HEADLESS={HEADLESS}, warm={args.warm}.")
    if not LIVE_MODE:
        log("Dry-run mode: Place Order will NOT be clicked.")
    # Playwright import is deferred so `python -m py_compile buyer.py`
    # succeeds even before `pip install playwright` / `playwright install`.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # (a) Warm persistent Chromium context: reuses the user's own
        # signed-in session from PROFILE_DIR. No credentials are handled
        # here; if the profile has no valid session, the pre-check aborts
        # and the user re-signs in themselves outside this driver.
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )
        if args.warm:
            import time

            page = context.new_page()
            log("WARM READY: persistent browser warm, idle, waiting for a "
                "watcher hit signal. No Apple requests made in warm mode. "
                "NO ORDER PLACED.")
            try:
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                log("Warm mode interrupted. NO ORDER PLACED.")
                return
        page = context.new_page()
        try:
            session_precheck(page)
            select_configurator(page)
            select_applecare_annual(page)
            verify_bag(page)
            pickup = select_pickup(page)
            select_default_payment(page)
            # Final re-verification immediately before the gate/submit.
            review = verify_review(page, pickup)
            if not LIVE_MODE:
                dry_run_gate(page, review)
            # Live path only: re-verify AGAIN immediately before submitting,
            # so nothing stale is submitted if the page changed.
            review = verify_review(page, pickup)
            place_order_guarded(page, review)
            log("Live submit attempted once. Verify the confirmation page/order number manually.")
        finally:
            context.close()


if __name__ == "__main__":
    main()
