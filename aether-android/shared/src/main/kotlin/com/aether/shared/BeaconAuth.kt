package com.aether.shared

import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * Authenticated beacon payload: build and verify.
 *
 * Wire format (19 bytes total, big-endian, fits BLE manufacturer-data):
 *
 *     [2B magic | 1B ver | 4B uid_hash | 4B counter | 8B HMAC-SHA256(realm_key, uid_hash||counter)]
 *
 * Mirrors `aether-bridge/beacon_auth.py` byte-for-byte. The HMAC is truncated
 * to its first 8 bytes to fit the BLE manufacturer-data budget; this is a
 * standard truncated-MAC construction providing 64-bit forgery resistance,
 * adequate for this phase's LAN-trust threat model (see PROTOCOL.md Security
 * Annex).
 *
 * Counter is monotonic per uid_hash and must be persisted so a process/device
 * restart does not reopen a replay window at counter=0 (confirmed product
 * decision) — see `BeaconCounterStore` on the Android side (DataStore).
 */
public object BeaconAuth {

    /** "Aether" shorthand marker. */
    private val MAGIC: ByteArray = byteArrayOf(0xAE.toByte(), 0x74.toByte())

    public const val CURRENT_VERSION: Int = 1

    /** 2 (magic) + 1 (ver) + 4 (uid_hash) + 4 (counter) + 8 (mac). */
    public const val PAYLOAD_LENGTH: Int = 19

    public const val MAC_LENGTH: Int = 8

    /**
     * Derive the 32-bit uid_hash carried in the beacon payload from a
     * beacon's stable identifying name.
     *
     * Single-user beacons only this phase (per confirmed product scope) —
     * the uid_hash field exists in the wire format for future multi-user
     * support, but election/ownership logic is untouched this phase.
     */
    public fun uidHashFromName(name: String): Long {
        val digest = MessageDigest.getInstance("SHA-256").digest(name.toByteArray(Charsets.UTF_8))
        return bytesToUnsignedInt(digest, 0)
    }

    private fun computeMac(realmKey: ByteArray, uidHash: Long, counter: Long): ByteArray {
        val message = ByteArray(8)
        writeUInt32BE(message, 0, uidHash)
        writeUInt32BE(message, 4, counter)

        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(realmKey, "HmacSHA256"))
        val fullMac = mac.doFinal(message)
        return fullMac.copyOf(MAC_LENGTH)
    }

    /** Build the 19-byte authenticated beacon payload. */
    public fun buildBeaconPayload(
        realmKey: ByteArray,
        uidHash: Long,
        counter: Long,
        ver: Int = CURRENT_VERSION,
    ): ByteArray {
        val mac = computeMac(realmKey, uidHash, counter)
        val payload = ByteArray(PAYLOAD_LENGTH)
        var offset = 0

        MAGIC.copyInto(payload, offset)
        offset += MAGIC.size

        payload[offset] = ver.toByte()
        offset += 1

        writeUInt32BE(payload, offset, uidHash)
        offset += 4

        writeUInt32BE(payload, offset, counter)
        offset += 4

        mac.copyInto(payload, offset)

        return payload
    }

    public data class BeaconVerifyResult(
        val ok: Boolean,
        val uidHash: Long? = null,
        val counter: Long? = null,
        /** null when ok; else one of: bad_length, bad_magic, bad_version, replay, hmac_mismatch, no_candidate_keys. */
        val reason: String? = null,
    )

    /**
     * Verify an authenticated beacon payload.
     *
     * Rejects (in order checked) on: wrong length, wrong magic, unsupported
     * version, replayed/stale counter (<= lastCounter), or HMAC mismatch.
     * Counter is checked before HMAC so a replayed-but-otherwise-valid
     * payload is rejected without needing a fresh HMAC computation.
     */
    public fun verifyBeacon(
        payload: ByteArray,
        realmKey: ByteArray,
        lastCounter: Long,
    ): BeaconVerifyResult {
        if (payload.size != PAYLOAD_LENGTH) {
            return BeaconVerifyResult(ok = false, reason = "bad_length")
        }

        if (payload[0] != MAGIC[0] || payload[1] != MAGIC[1]) {
            return BeaconVerifyResult(ok = false, reason = "bad_magic")
        }

        val ver = payload[2].toInt() and 0xFF
        if (ver != CURRENT_VERSION) {
            return BeaconVerifyResult(ok = false, reason = "bad_version")
        }

        val uidHash = bytesToUnsignedInt(payload, 3)
        val counter = bytesToUnsignedInt(payload, 7)
        val receivedMac = payload.copyOfRange(11, 19)

        if (counter <= lastCounter) {
            return BeaconVerifyResult(ok = false, reason = "replay")
        }

        val expectedMac = computeMac(realmKey, uidHash, counter)
        if (!constantTimeEquals(receivedMac, expectedMac)) {
            return BeaconVerifyResult(ok = false, reason = "hmac_mismatch")
        }

        return BeaconVerifyResult(ok = true, uidHash = uidHash, counter = counter)
    }

    /**
     * Verify against a list of candidate realm keys (current key first, then
     * grace-window history), so a beacon signed just before a realm key
     * rotation is not rejected mid-flight — see realm.py's grace-window
     * design and PROTOCOL.md Security Annex.
     */
    public fun verifyBeaconAnyKey(
        payload: ByteArray,
        candidateKeys: List<ByteArray>,
        lastCounter: Long,
    ): BeaconVerifyResult {
        if (candidateKeys.isEmpty()) {
            return BeaconVerifyResult(ok = false, reason = "no_candidate_keys")
        }

        var firstResult: BeaconVerifyResult? = null
        for (key in candidateKeys) {
            val result = verifyBeacon(payload, key, lastCounter)
            if (firstResult == null) {
                firstResult = result
            }
            if (result.ok) {
                return result
            }
        }
        return firstResult!!
    }

    /** Writes [value] as an unsigned big-endian 4-byte int into [dest] at [offset]. */
    private fun writeUInt32BE(dest: ByteArray, offset: Int, value: Long) {
        dest[offset] = ((value ushr 24) and 0xFF).toByte()
        dest[offset + 1] = ((value ushr 16) and 0xFF).toByte()
        dest[offset + 2] = ((value ushr 8) and 0xFF).toByte()
        dest[offset + 3] = (value and 0xFF).toByte()
    }

    /** Reads 4 bytes from [src] at [offset] as an unsigned big-endian int, widened to Long. */
    private fun bytesToUnsignedInt(src: ByteArray, offset: Int): Long {
        return ((src[offset].toLong() and 0xFF) shl 24) or
            ((src[offset + 1].toLong() and 0xFF) shl 16) or
            ((src[offset + 2].toLong() and 0xFF) shl 8) or
            (src[offset + 3].toLong() and 0xFF)
    }

    /** Constant-time byte array comparison, mirroring Python's `hmac.compare_digest`. */
    private fun constantTimeEquals(a: ByteArray, b: ByteArray): Boolean {
        if (a.size != b.size) return false
        var result = 0
        for (i in a.indices) {
            result = result or (a[i].toInt() xor b[i].toInt())
        }
        return result == 0
    }
}
