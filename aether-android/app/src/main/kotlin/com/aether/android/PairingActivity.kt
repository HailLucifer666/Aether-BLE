package com.aether.android

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.util.Size
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import com.aether.shared.PairingClient
import com.google.crypto.tink.subtle.Ed25519Sign
import com.google.mlkit.vision.barcode.BarcodeScanning
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.common.InputImage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.util.concurrent.Executors

/**
 * QR pairing (join side): scans the QR code rendered by Phase 6's
 * `pairing.py` offering side, decodes `{pubkey, mdns_name, realm_invite_token}`,
 * then hands off to [PairingClient] to complete the mutual Ed25519 exchange
 * over the peer's local TCP listener (`PairingCeremony`).
 */
public class PairingActivity : ComponentActivity() {

    @Serializable
    private data class PairingOfferJson(
        val pubkey: String,
        val mdns_name: String,
        val realm_invite_token: String,
    )

    private val pairingClient = PairingClient()
    private val preferences by lazy { AetherPreferences(applicationContext) }

    private val cameraPermissionRequest = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        cameraPermissionGranted = granted
    }

    private var cameraPermissionGranted by mutableStateOf(false)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        cameraPermissionGranted = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        if (!cameraPermissionGranted) {
            cameraPermissionRequest.launch(Manifest.permission.CAMERA)
        }

        setContent {
            var statusText by remember { mutableStateOf("Point camera at the pairing QR code") }

            if (cameraPermissionGranted) {
                QrScannerView(
                    onQrDecoded = { raw -> handleDecodedQr(raw) { status -> statusText = status } },
                )
            }
            Text(statusText)
        }
    }

    private fun handleDecodedQr(raw: String, onStatus: (String) -> Unit) {
        val offer = try {
            Json.decodeFromString<PairingOfferJson>(raw)
        } catch (e: Exception) {
            onStatus("Invalid QR payload: ${e.message}")
            return
        }

        onStatus("Connecting to ${offer.mdns_name}...")

        CoroutineScope(Dispatchers.IO).launch {
            val ownPublicKeyHex = getOrCreateOwnKeyPair().publicKey.toHex()

            // The peer's TCP listener host/port is resolved via mDNS in the
            // full flow; this phase assumes the pairing QR's mdns_name
            // resolves to the peer's LAN address via the platform's mDNS
            // resolver, matching pairing.py's own local-network trust model.
            val result = pairingClient.pair(
                host = offer.mdns_name,
                port = PAIRING_PORT,
                offer = PairingClient.PairingOffer(
                    pubkeyHex = offer.pubkey,
                    mdnsName = offer.mdns_name,
                    realmInviteToken = offer.realm_invite_token,
                ),
                ownPublicKeyHex = ownPublicKeyHex,
            )

            val status = when (result) {
                is PairingClient.PairingResult.Success -> {
                    // pairing.py's PairingCeremony wire protocol (see pairing.py docstring)
                    // never sends a signature to verify - it only echoes back its own pubkey
                    // after checking the invite token. verifyPeerSignature() therefore has no
                    // signed message to check on this wire; the invite-token match already
                    // performed server-side is the only trust anchor Phase 6 provides today.
                    // A real mutual-auth signature exchange needs a Phase 6.5 wire change.
                    if (result.peerPubkeyHex.isValidEd25519PublicKeyHex()) {
                        // TODO(Phase 6.5 wire gap): pairing.py's PairingCeremony response carries
                        // only {"pubkey": hex} - no realm_key/realm_key_v field exists on the wire
                        // yet (despite phase7/ARCHITECTURE.md describing one), so there is nothing
                        // real to pass to preferences.setRealm() here. Wire this call in once the
                        // Python side actually sends realm key material post-handshake.
                        "Paired with peer ${result.peerPubkeyHex.take(12)}..."
                    } else {
                        "Pairing failed: malformed_peer_pubkey"
                    }
                }
                is PairingClient.PairingResult.Failure -> "Pairing failed: ${result.reason}"
            }
            runOnUiThread { onStatus(status) }
        }
    }

    /**
     * Returns this device's persisted Ed25519 pairing identity, generating it on first use.
     * The persisted private key is the 32-byte Ed25519 seed, so it round-trips through
     * [Ed25519Sign.KeyPair.newKeyPairFromSeed] - Tink's [Ed25519Sign.KeyPair] has no
     * constructor that takes an existing key pair directly.
     */
    private suspend fun getOrCreateOwnKeyPair(): Ed25519Sign.KeyPair {
        val existingHex = preferences.ownEd25519PrivateKeyHex.first()
        if (existingHex != null) {
            return Ed25519Sign.KeyPair.newKeyPairFromSeed(existingHex.hexToBytes())
        }
        val keyPair = Ed25519Sign.KeyPair.newKeyPair()
        preferences.setOwnEd25519PrivateKeyHex(keyPair.privateKey.toHex())
        return keyPair
    }

    private companion object {
        /** Default port for pairing.py's PairingCeremony local listener. */
        const val PAIRING_PORT = 8765
    }
}

private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

private fun String.hexToBytes(): ByteArray {
    val clean = trim()
    val out = ByteArray(clean.length / 2)
    for (i in out.indices) {
        val index = i * 2
        out[i] = ((Character.digit(clean[index], 16) shl 4) + Character.digit(clean[index + 1], 16)).toByte()
    }
    return out
}

/** Ed25519 raw public keys are exactly 32 bytes; guards against a malformed/empty peer response. */
private fun String.isValidEd25519PublicKeyHex(): Boolean =
    length == 64 && all { it.isDigit() || it.lowercaseChar() in 'a'..'f' }

@Composable
private fun QrScannerView(onQrDecoded: (String) -> Unit) {
    val context = LocalContext.current
    var decoded by remember { mutableStateOf(false) }

    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { ctx ->
            val previewView = PreviewView(ctx)
            val cameraProviderFuture = ProcessCameraProvider.getInstance(ctx)
            val executor = Executors.newSingleThreadExecutor()
            val scanner = BarcodeScanning.getClient()

            cameraProviderFuture.addListener({
                val cameraProvider = cameraProviderFuture.get()
                val preview = Preview.Builder().build().also {
                    it.setSurfaceProvider(previewView.surfaceProvider)
                }

                val analysis = ImageAnalysis.Builder()
                    .setTargetResolution(Size(1280, 720))
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .build()

                analysis.setAnalyzer(executor) { imageProxy ->
                    val mediaImage = imageProxy.image
                    if (mediaImage != null && !decoded) {
                        val image = InputImage.fromMediaImage(mediaImage, imageProxy.imageInfo.rotationDegrees)
                        scanner.process(image)
                            .addOnSuccessListener { barcodes ->
                                val value = barcodes.firstOrNull { it.valueType == Barcode.TYPE_TEXT }?.rawValue
                                    ?: barcodes.firstOrNull()?.rawValue
                                if (value != null && !decoded) {
                                    decoded = true
                                    onQrDecoded(value)
                                }
                            }
                            .addOnCompleteListener { imageProxy.close() }
                    } else {
                        imageProxy.close()
                    }
                }

                try {
                    cameraProvider.unbindAll()
                    cameraProvider.bindToLifecycle(
                        ctx as androidx.lifecycle.LifecycleOwner,
                        CameraSelector.DEFAULT_BACK_CAMERA,
                        preview,
                        analysis,
                    )
                } catch (e: Exception) {
                    // Camera bind failed (e.g. no back camera) - preview stays blank,
                    // user can back out; nothing further to recover here.
                }
            }, ContextCompat.getMainExecutor(ctx))

            previewView
        },
    )
}
