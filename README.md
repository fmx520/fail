# iPhone Duo pickup watcher — ready to configure, not yet running

## Verified before building (2026-10-30, live Apple response for ZIP 11754)
- Endpoint: `apple.com/shop/retail/pickup-message` with `pl=true`, `mts.0=regular`,
  `location=11754`, and all parts in ONE request. It returns the nearest stores
  with `storeName`, `storeNumber`, and `partsAvailability` per part.
- Stores in that response, in priority order: Smith Haven (R139), Walt Whitman
  (R068), Roosevelt Field (R060), Manhasset (R327). A third-party "SoNo Collection"
  (CT) store sits between Walt Whitman and Roosevelt Field in distance order —
  the filter is by exact storeName, not by position, so it is ignored.
- Apple-confirmed part titles for 512GB / 1TB:
  - MK1G4LL/A — iPhone Duo 512GB Star White
  - MK1H4LL/A — iPhone Duo 512GB Night Sky
  - MK1J4LL/A — iPhone Duo 1TB Star White
  - MK1L4LL/A — iPhone Duo 1TB Night Sky
  All four read "Currently unavailable" at all four stores at check time.
- 256GB / 2TB part numbers are NOT yet verified against Apple: third-party
  listings disagree with each other (one lists MK1E4LL/A as Star White, another
  as Night Sky). Do not add a 256GB/2TB part until the watcher prints Apple's
  own `storePickupProductTitle` for it and it matches the intended config.
  The watcher prints that title on every hit, and the code uses it as the
  config name, so a wrong part fails visibly instead of silently watching
  the wrong phone.

## Three things the original spec gets wrong — fixed in the code
1. **"One request per config"** — the endpoint accepts many `parts.N` in one
   request. Batching is both faster and less likely to trip Apple's limits.
2. **Polling every 10–15s is not proven safe.** An independent watcher
   reported Apple blocking lookups as often as once a minute (HTTP 541 with a
   misleading "Page Not Found" page), with backoff 5 → 10 → 20 → 30 min.
   This code keeps the spec's 12s default but backs off on ANY error to
   60s, then doubles to a 300s cap, and — critically — never treats an
   error as "sold out". If the first day shows 541s, raise POLL_INTERVAL_SECONDS
   to 30–60 and re-test; missing a drop because you are blocked is worse
   than a 45-second-later alert.
3. **"Opens straight to checkout in the Apple Store app" is unproven.**
   `applestore://` is documented only as opening the app, with no verified
   product-deep-link path. The alert link is the web buy page
   (`apple.com/shop/buy-iphone/iphone-duo`), which on iOS normally offers
   to open in the Apple Store app as a universal link. Test this on your
   phone before relying on it — it is an acceptance-test item, not a fact.

## Push choice
- **Pushover (recommended):** priority 2 is emergency — it retries every
  30s until acknowledged (expire 600s here) and bypasses Pushover quiet
  hours. That is the closest thing to a Focus/silent breakthrough from a
  third-party push service. Needs: a Pushover app token + your user key,
  and the Pushover app installed with notifications allowed.
- **ntfy:** free and self-hostable, but its iOS app has historically lacked
  Apple's critical-alert entitlement, so a "critical" ntfy push may arrive
  at normal priority and not break silent mode. Only pick ntfy if you test
  it on your phone first with Focus on.

## Hosting
- Render background workers are paid — free Render does not cover them.
  Budget ~$7/mo for the smallest always-on Render service, plus Supabase
  free tier for the `drops` table (run schema.sql there). Do not use a
  free Render web service for this: it spins down after 15 min idle.
- The only secret is the push token/user key (Pushover) or the ntfy topic.
  No Apple ID, no payment info, nothing else is stored.

## To start (once config is chosen)
1. `python3 -m py_compile watcher.py` (no dependencies for the JSONL path)
2. Set env: `PARTS`, `PUSHOVER_TOKEN`, `PUSHOVER_USER` (or `NTFY_TOPIC`),
   optionally `DATABASE_URL` and `POLL_INTERVAL_SECONDS`.
3. Run it, then force a test: temporarily point PARTS at a config that is
   in stock somewhere, or run a dry-run hit by hand — the "done when" test
   in the spec (critical push within 15s of detection, tap lands on the
   right product, one full available → sold-out cycle logged) still has to
   be performed on your phone. Nothing here claims it already passed.

## Scope, decided 2026-10-30
Existing Vercel Duo Tracker handles availability alerts. This Render
worker is for AUTO-CHECKOUT ONLY: `CHECKOUT_ONLY=true` suppresses
duplicate availability alerts, and Pushover is used only for checkout
bought/failed results. `checkout.py` is a stub until a real driver passes.

## Deploy to Render + Supabase (package ready 2026-10-30)
- `render.yaml` is a background worker running `python3 watcher.py`.
  It is paid on Render (free tier does not cover workers) and it starts
  with `AUTO_CHECKOUT_ENABLED=false`.
- Supabase: create a project, run `schema.sql`, paste its Postgres URL
  as `DATABASE_URL` in Render.
- Pushover: create an app at pushover.net for the token, take your user
  key from your Pushover dashboard, install the Pushover app on your
  phone. Muse's vault cannot store Pushover (it needs two secrets, token
  + user key, and the vault deposits exactly one), so set both values
  directly in Render's env vars. Never send them in chat.
- Auto checkout: NOT enabled yet, on purpose. The pickup endpoint can
  check stock but cannot buy. A checkout driver still needs a proven
  Apple session, payment on the account, and a 2FA plan. Deploy the
  watcher first, prove the push, then enable checkout only after a
  dry-run checkout setup passes. Defaults assumed unless corrected:
  unlocked / connect later, no AppleCare, no trade-in, $2,199 + tax cap,
  stores in priority order, earliest slot.

## Still needed from Parker
- Primary config: CONFIRMED 2026-10-30 — 512GB Night Sky, MK1H4LL/A, $2,199.
  Watcher default is now that part only.
- Backups: NONE — Parker confirmed 2026-10-30, 512GB Night Sky only.
- Push/hosting: CONFIRMED 2026-10-30 — Pushover + Render/Supabase.
- AUTO CHECKOUT (requested 2026-10-30, not built): supersedes notify-only.
  The pickup endpoint can only CHECK stock, it cannot buy. Buying needs an
  authenticated Apple checkout session, a payment method on that account,
  carrier choice, and a pickup slot — plus handling for Apple 2FA / session
  expiry / bot checks. Pushover token and Render deployment are paused,
  not dropped, until the checkout details below are confirmed.
