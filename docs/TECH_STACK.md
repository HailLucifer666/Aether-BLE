# Tech Stack — Phase 6

- **Language:** Python 3.11 (matches existing `aether-bridge/`), no new runtime.
- **Crypto:** `cryptography` (Ed25519 signing, HMAC-SHA256) — add to `requirements.txt`.
- **Discovery:** `zeroconf` (pure-Python mDNS/DNS-SD) — add to `requirements.txt`.
- **QR generation/read:** `qrcode` for generation (terminal/PNG); reading happens on the pairing device (out of scope — Android app is Phase 7), so Phase 6 only needs to *display* a QR the future app will scan. For now, print the pairing payload as a QR PNG + raw string fallback.
- **Persistence:** flat JSON file per node (e.g. `~/.aether/identity.json`, `~/.aether/realm.json`) — no database. Matches project's zero-infra philosophy.
- **No new frontend work this phase** — dashboard is unaffected (new message types are additive per `messages.py`'s existing pattern).

Rationale: every choice reuses what's already in the repo (Python, no DB, no cloud) rather than introducing new infra. `cryptography` and `zeroconf` are the two additions.
