# Code Review — Phase 6: Security & Discovery (Final Review Gate)

**Reviewer role:** last-line-of-defense code review, independent of QA and Security sign-offs.
**Verdict: PASS**

---

## Summary

Phase 6 replaces plaintext BLE name matching with an authenticated, replay-proof beacon
protocol (HMAC-SHA256 + monotonic counter), adds Ed25519 node identity, QR pairing, mDNS
discovery, and a `hello` version/capability handshake — exactly as scoped in
`docs/PRD.md` / `docs/FEATURES.md` / `docs/ARCHITECTURE.md`. The one HIGH finding raised
by the security audit (grace-window key rotation documented but not wired into the live
beacon-verification path) was fixed after the audit ran. I independently verified that
fix by reading the changed code paths and the new regression tests myself, and by
re-running the full test suite from a clean shell. Both check out. I also independently
verified the PRD/architecture scope boundary using `git diff` against the pre-Phase-6
commit, rather than trusting the stated "explicitly NOT touched" file list.

---

## 1. Independent verification of the HIGH-finding fix (grace-window key rotation)

**Original gap (per `docs/SECURITY_AUDIT.md`):** `realm.py` implements `key_history` and
`verify_key()` for grace-window rotation, but `Bridge.__init__` only ever read
`realm.current_key` (a single fixed key), and `_on_advertisement` called
`verify_beacon(payload, self.realm_key, last_counter)` — never consulting `key_history`.
A `rotate_key()` call would instantly reject every node still broadcasting with the
previous key, contradicting `PROTOCOL.md` §11.3's "accepted against any of the last N key
versions" promise.

**Verification performed (read the code directly, not the description):**

- `aether-bridge/beacon_auth.py:107-130` — new `verify_beacon_any_key(payload,
  candidate_keys, last_counter)` iterates `candidate_keys` in order, returns the first
  `ok=True` result from `verify_beacon`, and otherwise returns the first candidate's
  failure. This is additive; `verify_beacon` itself (lines 72-104) is untouched —
  confirmed by diff, not just by reading the current file.
- `aether-bridge/bridge.py:113-115,137-145` — `Bridge.__init__` gained a `key_history`
  parameter. When `realm_key` is injected (test path), `candidate_keys` defaults to
  `[realm_key]` if `key_history` isn't also passed — this is byte-for-byte the pre-fix
  behavior, so no existing test's fixture setup silently changed meaning. In production
  (`realm_key is None`), it loads the full `Realm` object via `load_or_create_realm()`
  and sets `candidate_keys = list(realm.key_history.values())` — the actual fix, since
  `Realm.key_history` is populated with `GRACE_WINDOW_VERSIONS = 3` entries by
  `rotate_key()` (`realm.py:45-56`), matching `PROTOCOL.md` §11.3's "N=3" exactly.
- `aether-bridge/bridge.py:170-171` — `_on_advertisement` now calls
  `verify_beacon_any_key(payload, self.candidate_keys, last_counter)` instead of the old
  single-key `verify_beacon` call. Confirmed via `git diff 0145c67 -- bridge.py`.
- **Regression tests, read and reasoned through, not skimmed:**
  - `test_grace_window_key_history_accepts_beacon_signed_with_old_key`
    (`tests/test_bridge_beacon_auth.py:118-150`) — constructs a `Bridge` with
    `realm_key=new_key`, `key_history=[new_key, old_key]`, builds a payload signed with
    `old_key`, and asserts the bridge's state updates (i.e., the beacon verifies). This
    is the actual regression the audit was worried about — it correctly fails against the
    pre-fix code (single-key `verify_beacon` would reject an `old_key`-signed payload with
    `hmac_mismatch`) and passes against the fix.
  - `test_key_not_in_history_is_rejected` (`tests/test_bridge_beacon_auth.py:153-179`) —
    constructs a `Bridge` with `key_history=[REALM_KEY]` (old key pruned out), signs a
    payload with a key that was never in history, and asserts the state does **not**
    update. This is the necessary counter-test proving the fix isn't "accept any key" —
    without it, a change that made `verify_beacon_any_key` accept everything would still
    pass the first new test alone.
  - Both tests drive `Bridge._on_advertisement` directly (not a mock of
    `verify_beacon_any_key`), so they exercise the real HMAC computation and the real
    candidate-key iteration — not a stubbed-out approval.

**Conclusion: the fix is correct, complete, and closes the exact gap the audit
described.** It is additive (existing `verify_beacon` untouched, existing test fixtures'
behavior preserved when `key_history` isn't passed), matches the `PROTOCOL.md` §11.3
grace-window contract precisely (N=3, current key tried first per the docstring's stated
order — though since the pruning window guarantees at most 3 keys and the check is "first
match wins" rather than "current key preferred," the order doesn't materially matter here
since exactly one candidate key can ever match a genuine payload), and is covered by
tests that discriminate between "fix works" and "fix over-accepts."

One minor, non-blocking observation: `verify_beacon_any_key` re-derives `last_counter`
once via `self.counter_store.get_last_counter(self.uid_hash)` in `bridge.py:170` and
applies the same `last_counter` across every candidate key tried in the loop
(`beacon_auth.py:124`). That's correct — the counter is per-`uid_hash`, not per-key — but
worth flagging only because a future maintainer extending this to multi-user beacons will
need to keep that invariant explicit (it already is, via the docstring on
`verify_beacon_any_key`, so no action needed now).

---

## 2. Independent test suite run

Ran directly, not taken from `docs/TESTING.md`:

```
aether-bridge/.venv/Scripts/python.exe -m pytest -q
```

- First run: `1 failed, 124 passed in 15.34s` — the failure was
  `test_aggregator.py::test_contest_fires_and_chirp_overrides_ble_owner`.
- Second run (immediately after, no code changes): `125 passed in 14.11s`.
- Ran the same test in isolation
  (`pytest tests/test_aggregator.py::test_contest_fires_and_chirp_overrides_ble_owner`):
  failed on its own, in under 2 seconds, with the assertion landing on
  `_last_fusion_reason == 'ble-only'` instead of a chirp-driven reason.

This reproduces exactly the signature both `docs/TESTING.md` and
`docs/SECURITY_AUDIT.md` describe: a timing-sensitive test in the tier-2 ranging/fusion
contest logic that is not consistently reproducible either in or out of the full suite —
it is not a simple "fails alone, passes in suite" pattern but a genuine timing flake
(this run failed in isolation and passed in the full suite; a different run could invert
that). Total collected test count is 125 (123 pre-Phase-6 baseline + 2 new grace-window
regression tests), matching the expected count exactly.

**Scope check — is this Phase 6's fault?** Confirmed no:
`git diff 0145c67 -- aether-dashboard/ simulated_scanner.py election.py conversation.py
ranging.py aggregator.py` produced **zero output** — none of these files changed at all
since the pre-Phase-6 commit. `grep` for `beacon_auth`, `realm`, `verify_beacon`, and
`candidate_keys` across `aggregator.py`, `election.py`, and `ranging.py` also returned
nothing — there is no code path connecting Phase 6's auth changes to the tier-2
ranging/fusion contest logic this flaky test exercises. The flake is pre-existing,
unrelated to Phase 6, and correctly out of scope per `docs/ARCHITECTURE.md`'s "explicitly
NOT touched" list.

---

## 3. PRD acceptance criteria — alignment check

| Criterion | Status |
|---|---|
| Beacon payload HMAC-SHA256(realm_key, uid_hash‖counter); bridge.py verifies HMAC + monotonic counter | Implemented — `beacon_auth.py`, wired into `bridge.py:_on_advertisement` |
| Replayed/tampered payload rejected, logged, never reaches messages.py/election flow | Implemented — verified by `verify_beacon`'s `counter <= last_counter` / HMAC checks and the silent-drop path in `bridge.py:172-177`; confirmed `election.py` untouched |
| Counter persists to disk per device, survives restart | Implemented — `BeaconCounterStore` (`beacon_auth.py:133-155`), tested by `test_counter_store_survives_restart_no_replay_window_reopens` |
| QR pairing: pubkey + mDNS name → mutual Ed25519 exchange → realm admission | Implemented — `pairing.py`, `identity.py`; scanning/Android side correctly stubbed per Features doc (Phase 7) |
| `hello {ver, node_id, capabilities, sig}` added; existing message types/consumers unmodified | Implemented — `messages.py` diff shows `build_hello_message` purely additive; no other builder touched |
| mDNS advertises `_aether._tcp.local`; second node finds first with zero config | Implemented — `discovery.py`, `zeroconf`-based |
| Transport encryption explicitly deferred, LAN-trust documented | Honored — `PROTOCOL.md` §11.4 states this explicitly; `bridge.py`'s `websockets.serve` confirmed still plain `ws://` by audit and unchanged by this review |
| Key loss → manual re-pair only, no recovery flow | Honored — `PROTOCOL.md` §11 "Key loss" subsection states this; no recovery code present |
| All 85+ existing tests still pass; new tests cover HMAC/replay/counter/handshake | Confirmed — 125 passed on rerun; new tests span exactly these areas plus the two grace-window regression tests |

All three confirmed deferrals (transport encryption, key-loss recovery, multi-user
beacons) are explicitly documented in `docs/FEATURES.md` item list, `PROTOCOL.md` §10/§11.4,
and `docs/ARCHITECTURE.md`'s "What is explicitly NOT built here" — not silently dropped.

**Scope boundary (ARCHITECTURE.md "explicitly NOT touched"):** verified via
`git diff --stat 0145c67 -- aether-dashboard/ simulated_scanner.py election.py
conversation.py ranging.py aggregator.py` → empty output. Zero changes to any of these
five paths. `git status` shows the only modified tracked files are `PROTOCOL.md`,
`aether-bridge/bridge.py`, `aether-bridge/messages.py`, `aether-bridge/requirements.txt`,
plus the expected set of new untracked Phase 6 files (`beacon_auth.py`, `realm.py`,
`identity.py`, `pairing.py`, `discovery.py`, `SECURITY.md`, and their tests). This matches
`docs/ARCHITECTURE.md`'s component list exactly.

---

## 4. Residual items carried from the security audit (not re-litigated, status confirmed)

These were already correctly triaged as non-blocking by the security audit; I confirmed
their state hasn't silently regressed or been mis-claimed as fixed:

- **MEDIUM — no `chmod 0600` on `identity.json`/`realm.json`/`beacon_counter.json`.**
  Confirmed still open: `identity.py:68` still calls plain `path.write_text(...)` with no
  permission hardening. Correctly left unfixed and un-claimed-as-fixed; remains a
  reasonable follow-up, not a blocker, given the documented solo-operator LAN-trust
  posture.
- **LOW — `cryptography>=42.0` unpinned ceiling.** Confirmed still `>=42.0` in
  `requirements.txt`. Hygiene item, not a live CVE exposure at current resolve time, not a
  blocker.
- **LOW — pairing listener loopback-only; non-constant-time token compare.** Out of scope
  this phase per `pairing.py`'s own docstring (Android/scanning side is Phase 7); correctly
  deferred.
- **LOW — `uid_hash` derived from beacon name, no per-device credential binding.**
  Correctly scoped as a single-user placeholder per PRD/Architecture; `election.py`
  confirmed untouched.

None of these change the PASS verdict; all were already honestly scoped by the security
audit as non-blockers, and nothing in this review found new instances of files claiming
they were fixed when they weren't.

---

## 5. Code quality notes (secondary to the above, no blockers found)

- `beacon_auth.py`, `realm.py`, `bridge.py` are well-commented with rationale tied back to
  `PROTOCOL.md` section numbers — comments explain *why*, not just *what*, and several
  explicitly flag "confirmed product decision" language consistent with the project's own
  `SECURITY.md` convention of marking decisions that must not be silently simplified away.
- `verify_beacon_any_key`'s "first candidate's failure is returned if all fail" behavior
  (`beacon_auth.py:130`) is a reasonable, cheap choice for this phase given the docstring's
  own reasoning (length/magic/version/replay reasons are key-independent since they're
  checked before the HMAC compare) — correctly reasoned, not just asserted.
- No new dead code introduced by the fix; `Realm.verify_key()` remains unused
  (`realm.py:36-43`) but that's pre-existing from before this fix and not something this
  fix was responsible for wiring up (the fix uses `key_history.values()` directly rather
  than `verify_key()` — a reasonable simplification, since `verify_key()`'s per-version
  signature isn't needed by `verify_beacon_any_key`'s "try each key" approach). Flagging
  only as an observation: if `verify_key()` stays permanently unused, it's a candidate for
  removal in a future cleanup pass, but that's out of scope for this review to demand now.
- Bridge constructor docstring/comment block (`bridge.py:124-136`) clearly documents the
  test-injection vs. production code path distinction — this is exactly the kind of
  comment that prevents a future maintainer from being confused about why
  `candidate_keys` defaults differently depending on whether `realm_key` was passed.

---

## Verdict: PASS

Phase 6 is aligned with `docs/PRD.md`, `docs/FEATURES.md`, and `docs/ARCHITECTURE.md`.
The self-identified and self-fixed HIGH finding from the security audit was verified
independently against the actual code and tests, not merely accepted on description, and
holds up. The full test suite passes (125/125 on a clean rerun); the one flaky test is
independently confirmed pre-existing, timing-related, and outside Phase 6's changed-file
set. The architecture's explicit "do not touch" boundary was verified with `git diff`
against the pre-Phase-6 commit and was honored with zero exceptions. Residual MEDIUM/LOW
findings from the security audit remain open but were already correctly scoped as
non-blocking follow-ups, not silently dropped or misrepresented as fixed.

No further action required to ship this phase as scoped. Recommended follow-ups (not
blockers): `chmod 0600` on the three key-material JSON files, and bumping the
`cryptography` floor above 42.0 at the next dependency touch.
