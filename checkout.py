"""Auto-checkout driver — NOT YET IMPLEMENTED, on purpose.

The pickup endpoint can check stock but cannot buy. A real driver needs:
- an authenticated Apple checkout session (established by Parker signing
  in in a browser, never by storing a password here),
- a payment method already default on that Apple account,
- carrier = unlocked / connect later, no AppleCare, no trade-in
  (assumed defaults, Parker to correct if different),
- price cap $2,199 + tax, stores in priority order, earliest slot,
- a 2FA plan for when Apple interrupts checkout.

Until that driver is built and passes a dry-run, attempt_checkout()
returns failure with that reason, so the watcher can never claim it
bought a phone it did not. The watcher then pushes the failure via
Pushover, and Parker can still buy manually from the existing
Duo Tracker alert.
"""

def attempt_checkout(store, part, title):
    """Return (success: bool, detail: str). Never raise for a missing driver."""
    return (False,
            "Checkout driver not configured yet — no order was placed. "
            "Buy manually from the Duo Tracker alert if the drop is still live.")
