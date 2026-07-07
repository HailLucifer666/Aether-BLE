package com.aether.shared

import kotlin.test.Test
import kotlin.test.assertEquals

/**
 * Cross-language parity test: asserts the Kotlin [BeaconAuth] implementation
 * produces byte-identical output to the Python reference `beacon_auth.py`
 * for the same inputs. This is the guardrail called out in
 * `docs/phase7/ARCHITECTURE.md` — if this test fails, the Kotlin
 * implementation is wrong, full stop, regardless of how reasonable the code
 * looks.
 */
class BeaconAuthTest {

    @Test
    fun `build beacon payload matches python reference test vector`() {
        val key = ByteArray(32) { 0x11 }
        val uidHash = 0xDEADBEEFL
        val counter = 42L

        val payload = BeaconAuth.buildBeaconPayload(key, uidHash, counter)

        val expectedHex = "ae7401deadbeef0000002ad86695bb34d03d6b"
        assertEquals(expectedHex, payload.toHex())
    }

    @Test
    fun `verify beacon accepts payload built with matching key and fresh counter`() {
        val key = ByteArray(32) { 0x11 }
        val uidHash = 0xDEADBEEFL
        val counter = 42L
        val payload = BeaconAuth.buildBeaconPayload(key, uidHash, counter)

        val result = BeaconAuth.verifyBeacon(payload, key, lastCounter = 0L)

        assertEquals(true, result.ok)
        assertEquals(uidHash, result.uidHash)
        assertEquals(counter, result.counter)
    }

    @Test
    fun `verify beacon rejects replayed counter`() {
        val key = ByteArray(32) { 0x11 }
        val payload = BeaconAuth.buildBeaconPayload(key, 0xDEADBEEFL, counter = 42L)

        val result = BeaconAuth.verifyBeacon(payload, key, lastCounter = 42L)

        assertEquals(false, result.ok)
        assertEquals("replay", result.reason)
    }

    @Test
    fun `verify beacon rejects hmac mismatch for wrong key`() {
        val key = ByteArray(32) { 0x11 }
        val wrongKey = ByteArray(32) { 0x22 }
        val payload = BeaconAuth.buildBeaconPayload(key, 0xDEADBEEFL, counter = 42L)

        val result = BeaconAuth.verifyBeacon(payload, wrongKey, lastCounter = 0L)

        assertEquals(false, result.ok)
        assertEquals("hmac_mismatch", result.reason)
    }

    private fun ByteArray.toHex(): String = joinToString(separator = "") { "%02x".format(it) }
}
