"""Buyer configuration for the iPhone Duo auto-checkout driver.

Source of truth for the FIXED target spec. Do not change the spec here
without explicit user confirmation.

Spec (fixed):
  - iPhone Duo, 512GB, Night Sky, unlocked / connect later
  - Apple part number: MK1H4LL/A
  - Phone price: $2,199 (before tax / before AppleCare)
  - No trade-in
  - AppleCare+ on the YEARLY payment option
  - Default payment method on the user's Apple account
  - Pickup stores, in priority order, earliest pickup slot
  - Try the next store if the first sells out mid-checkout
  - Total = phone + AppleCare yearly + tax (NO $2,199 total cap)

Price provenance:
  - Phone $2,199: user-confirmed.
  - AppleCare+ $199.99/yr (with $19.99/mo as the alternative): REPORTED
    only, NOT verified. Marked UNVERIFIED-UNTIL-DRY-RUN below.
    The buyer MUST read the actual annual price off the review page
    during a supervised dry run and MUST NEVER hard-code an AppleCare
    part number (none is known/verified).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Target product
# ---------------------------------------------------------------------------

PART_NUMBER = "MK1H4LL/A"
PRODUCT_TITLE_CONTAINS = "iPhone Duo"  # TO-VERIFY-IN-DRY-RUN: exact bag title text
STORAGE = "512GB"
FINISH = "Night Sky"
PHONE_PRICE_USD = 2199.00  # user-confirmed, before tax / before AppleCare

CARRIER_OPTION = "connect-later"  # unlocked / connect later, no carrier financing
TRADE_IN = "none"  # no trade-in

# ---------------------------------------------------------------------------
# AppleCare+ — REPORTED, UNVERIFIED-UNTIL-DRY-RUN
# ---------------------------------------------------------------------------
# Reported values only. The dry run must confirm the real annual selection
# value/label from Apple's own checkout UI. If the dry-run value differs,
# update this file with the observed value and re-verify — do not silently
# accept a different AppleCare tier/term.

APPLECARE_ANNUAL_REPORTED_USD = 199.99  # UNVERIFIED-UNTIL-DRY-RUN
APPLECARE_MONTHLY_REPORTED_USD = 19.99  # UNVERIFIED-UNTIL-DRY-RUN, alternative only
APPLECARE_TERM = "yearly"  # must select the YEARLY option, never monthly
APPLECARE_PART_NUMBER = None  # UNKNOWN — never hard-code; read from review page if shown

# Total rule: phone + AppleCare yearly + tax. Tax varies by store/zip, so
# the buyer verifies line items + that total >= phone + AppleCare yearly,
# and never enforces a $2,199 total cap.
EXPECTED_MIN_TOTAL_USD = PHONE_PRICE_USD + APPLECARE_ANNUAL_REPORTED_USD

# ---------------------------------------------------------------------------
# Pickup stores, in priority order (do not reorder without user confirmation)
# ---------------------------------------------------------------------------
# Store numbers are as specified for this task. Store display names are
# used only for logging / matching review-page text.

STORES_IN_PRIORITY_ORDER = [
    {"name": "Smith Haven", "number": "R139"},
    {"name": "Walt Whitman", "number": "R068"},
    {"name": "Roosevelt Field", "number": "R060"},
    {"name": "Manhasset", "number": "R327"},
]

# ---------------------------------------------------------------------------
# Timeouts (milliseconds, Playwright convention)
# ---------------------------------------------------------------------------

TIMEOUT_NAV_MS = 60_000
TIMEOUT_SELECTOR_MS = 30_000
TIMEOUT_PICKUP_MS = 30_000
TIMEOUT_REVIEW_MS = 30_000

# ---------------------------------------------------------------------------
# Abort reasons — every abort path must log "NO ORDER PLACED" + one of these
# (plus detail). See buyer.py: abort().
# ---------------------------------------------------------------------------

ABORT_SESSION_EXPIRED = "SESSION_EXPIRED — needs user re-sign-in"
ABORT_NOT_SIGNED_IN = "NOT_SIGNED_IN — no active Apple session"
ABORT_BOT_CHALLENGE = "BOT_CHALLENGE — automated-access challenge detected"
ABORT_TWO_FACTOR = "TWO_FACTOR_PROMPT — 2FA / verification code requested"
ABORT_PART_MISMATCH = "PART_MISMATCH — bag part is not MK1H4LL/A"
ABORT_PRICE_MISMATCH = "PRICE_MISMATCH — bag/review price does not match spec"
ABORT_CONFIG_MISMATCH = "CONFIG_MISMATCH — storage/finish/carrier/trade-in mismatch"
ABORT_APPLECARE_MISMATCH = "APPLECARE_MISMATCH — annual AppleCare not selected/verified"
ABORT_STORE_SOLD_OUT = "STORE_SOLD_OUT — all priority stores sold out at pickup step"
ABORT_SLOT_UNAVAILABLE = "SLOT_UNAVAILABLE — no pickup slot at any priority store"
ABORT_PAYMENT_MISMATCH = "PAYMENT_MISMATCH — default payment not selected/verified"
ABORT_TOTAL_MISMATCH = "TOTAL_MISMATCH — review total does not match phone+AppleCare+tax rule"
ABORT_DRY_RUN_GATE = "DRY_RUN_GATE — stopped before Place Order by design"
ABORT_LIVE_NOT_ENABLED = "LIVE_NOT_ENABLED — live mode not explicitly enabled"
ABORT_ALREADY_SUBMITTED = "ALREADY_SUBMITTED — refusing second Place Order click"
ABORT_SELECTOR_UNVERIFIED = "SELECTOR_UNVERIFIED — placeholder selector needs dry-run verification"
ABORT_UNKNOWN = "UNKNOWN — aborted for an unclassified reason"
