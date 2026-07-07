package com.aether.shared

import com.google.crypto.tink.subtle.Ed25519Sign
import com.google.crypto.tink.subtle.Ed25519Verify
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.Socket
import java.nio.charset.StandardCharsets

/**
 * Joining/scanning-side counterpart to Phase 6's `pairing.py` `PairingCeremony`
 * (the offering side). Connects to the peer's local TCP listener advertised in
 * the pairing QR code and performs the mutual Ed25519 exchange described in
 * `pairing.py`'s docstring:
 *
 *   peer -> server: {"pubkey": "<hex>", "realm_invite_token": "<token>"}
 *   server -> peer: {"pubkey": "<hex>"}                    on success
 *                    {"error": "invalid_invite_token"}      on failure
 *
 * Protocol is newline-delimited JSON, one exchange per connection.
 */
public class PairingClient(
    private val socketFactory: (host: String, port: Int) -> Socket = { host, port -> Socket(host, port) },
) {

    /** Decoded contents of the pairing QR code rendered by `pairing.py`. */
    public data class PairingOffer(
        val pubkeyHex: String,
        val mdnsName: String,
        val realmInviteToken: String,
    )

    public sealed class PairingResult {
        public data class Success(val peerPubkeyHex: String) : PairingResult()
        public data class Failure(val reason: String) : PairingResult()
    }

    /**
     * Connects to [host]:[port] (the offering peer's `PairingCeremony` TCP
     * listener), sends this device's own Ed25519 public key plus the
     * [offer]'s invite token, and returns the peer's response.
     *
     * This method performs only the wire exchange; realm key retrieval is a
     * separate, higher-level step once the caller has established mutual
     * trust (out of scope for this class per `pairing.py`'s own scope note
     * that the offering side hands off realm membership to its caller).
     */
    public fun pair(
        host: String,
        port: Int,
        offer: PairingOffer,
        ownPublicKeyHex: String,
        connectTimeoutMs: Int = 5000,
        readTimeoutMs: Int = 5000,
    ): PairingResult {
        val socket = try {
            socketFactory(host, port).apply { soTimeout = readTimeoutMs }
        } catch (e: IOException) {
            return PairingResult.Failure("connect_failed: ${e.message}")
        }

        return socket.use {
            try {
                val request = buildRequestJson(ownPublicKeyHex, offer.realmInviteToken)
                writeLine(it.getOutputStream(), request)

                val reader = BufferedReader(InputStreamReader(it.getInputStream(), StandardCharsets.UTF_8))
                val responseLine = reader.readLine()
                    ?: return@use PairingResult.Failure("no_response")

                parseResponse(responseLine)
            } catch (e: IOException) {
                PairingResult.Failure("io_error: ${e.message}")
            }
        }
    }

    /** Verifies that [signature] over [message] was produced by [peerPublicKeyHex]. */
    public fun verifyPeerSignature(peerPublicKeyHex: String, message: ByteArray, signature: ByteArray): Boolean {
        return try {
            val verifier = Ed25519Verify(hexToBytes(peerPublicKeyHex))
            verifier.verify(signature, message)
            true
        } catch (e: Exception) {
            false
        }
    }

    /** Signs [message] with this device's own Ed25519 private key. */
    public fun sign(privateKey: Ed25519Sign, message: ByteArray): ByteArray = privateKey.sign(message)

    private fun writeLine(output: OutputStream, line: String) {
        output.write((line + "\n").toByteArray(StandardCharsets.UTF_8))
        output.flush()
    }

    private fun buildRequestJson(pubkeyHex: String, realmInviteToken: String): String {
        return "{\"pubkey\":\"${escapeJson(pubkeyHex)}\",\"realm_invite_token\":\"${escapeJson(realmInviteToken)}\"}"
    }

    private fun parseResponse(line: String): PairingResult {
        val errorMatch = Regex("\"error\"\\s*:\\s*\"([^\"]*)\"").find(line)
        if (errorMatch != null) {
            return PairingResult.Failure(errorMatch.groupValues[1])
        }
        val pubkeyMatch = Regex("\"pubkey\"\\s*:\\s*\"([^\"]*)\"").find(line)
        if (pubkeyMatch != null) {
            return PairingResult.Success(pubkeyMatch.groupValues[1])
        }
        return PairingResult.Failure("malformed_response")
    }

    private fun escapeJson(value: String): String = value.replace("\\", "\\\\").replace("\"", "\\\"")

    private fun hexToBytes(hex: String): ByteArray {
        val clean = hex.trim()
        val out = ByteArray(clean.length / 2)
        for (i in out.indices) {
            val index = i * 2
            out[i] = ((Character.digit(clean[index], 16) shl 4) + Character.digit(clean[index + 1], 16)).toByte()
        }
        return out
    }
}
