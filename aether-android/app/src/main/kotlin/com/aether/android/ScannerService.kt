package com.aether.android

import android.app.Notification
import android.app.Service
import android.bluetooth.BluetoothManager
import android.bluetooth.le.BluetoothLeScanner
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.aether.shared.Messages
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

/**
 * Foreground service that scans for BLE beacons, extracts the manufacturer-data
 * payload, and forwards raw+smoothed RSSI to the aggregator WebSocket using the
 * same JSON shape `aether-bridge/messages.py` defines (`type: "reading"` /
 * `"lost"`) - see ARCHITECTURE.md data flow - scanner. No changes to
 * `messages.py`/`aggregator.py` are required; this service is just another
 * producer of the same wire format `bridge.py` already produces.
 */
public class ScannerService : Service() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var scanner: BluetoothLeScanner? = null
    private var webSocket: WebSocket? = null
    private var lostCheckJob: Job? = null
    private lateinit var preferences: AetherPreferences
    private val httpClient = OkHttpClient()

    /** peer name -> last-seen state, used both for the "lost" sweep and the Settings RSSI table. */
    private val peers = mutableMapOf<String, PeerState>()

    override fun onCreate() {
        super.onCreate()
        preferences = AetherPreferences(applicationContext)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForegroundCompat()

        val adapter = getSystemService(BluetoothManager::class.java)?.adapter
        scanner = adapter?.bluetoothLeScanner

        serviceScope.launch { connectWebSocket() }
        startScanning()

        if (lostCheckJob?.isActive != true) {
            lostCheckJob = serviceScope.launch { lostSweepLoop() }
        }

        return START_STICKY
    }

    private suspend fun connectWebSocket() {
        val url = preferences.aggregatorWsUrl.firstOrNull() ?: return
        val request = Request.Builder().url(url).build()
        webSocket = httpClient.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {}
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {}
        })
    }

    private fun startScanning() {
        val scanner = this.scanner ?: return
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()

        try {
            scanner.startScan(emptyList(), settings, scanCallback)
        } catch (e: SecurityException) {
            // BLUETOOTH_SCAN not granted; service stays alive, user can grant from Settings.
        }
    }

    private val scanCallback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) {
            handleScanResult(result)
        }
    }

    private fun handleScanResult(result: ScanResult) {
        val manufacturerData = result.scanRecord?.getManufacturerSpecificData(BeaconService.MANUFACTURER_ID)
            ?: return

        // Peer identity for the reading/lost message contract is derived from
        // the device address, since uid_hash -> display name resolution is a
        // realm-membership concern out of scope for the raw scanner producer
        // (bridge.py's own scanner similarly reports by whatever identity it
        // has on hand; the aggregator is the layer that resolves names).
        val peerName = result.device.address ?: return
        val rawRssi = result.rssi.toDouble()
        val nowMs = System.currentTimeMillis()

        // Manufacturer-data payload confirms this is an Aether beacon (BLE
        // manufacturer-data ID 0xFFFF); authenticated verification against a
        // realm key is a bridge.py/aggregator.py-side concern, so the raw
        // bytes aren't otherwise used by this producer.
        if (manufacturerData.isEmpty()) return

        val previous = peers[peerName]
        val smoothed = if (previous == null) {
            rawRssi
        } else {
            SMOOTHING_ALPHA * rawRssi + (1 - SMOOTHING_ALPHA) * previous.smoothedRssi
        }

        peers[peerName] = PeerState(rawRssi = rawRssi, smoothedRssi = smoothed, lastSeenAtMs = nowMs)
        _livePeers.value = peers.mapValues { it.value.rawRssi to it.value.smoothedRssi }

        val message = Messages.buildReadingMessage(
            scanner = androidScannerId(),
            name = peerName,
            rawRssi = rawRssi,
            smoothedRssi = smoothed,
            lastSeenMs = nowMs - (previous?.lastSeenAtMs ?: nowMs),
        )
        webSocket?.send(message)
    }

    private suspend fun lostSweepLoop() {
        while (true) {
            delay(LOST_CHECK_INTERVAL_MS)
            val now = System.currentTimeMillis()
            val lost = peers.filterValues { now - it.lastSeenAtMs > LOST_THRESHOLD_MS }.keys.toList()
            for (name in lost) {
                peers.remove(name)
                _livePeers.value = peers.mapValues { it.value.rawRssi to it.value.smoothedRssi }
                webSocket?.send(Messages.buildLostMessage(scanner = androidScannerId(), name = name))
            }
        }
    }

    private fun androidScannerId(): String = "android-${Build.MODEL}"

    private fun startForegroundCompat() {
        val notification: Notification = NotificationCompat.Builder(this, AetherApplication.SCANNER_CHANNEL_ID)
            .setContentTitle("Aether scanner active")
            .setContentText("Scanning for nearby beacons")
            .setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
            .setOngoing(true)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        super.onDestroy()
        scanner?.stopScan(scanCallback)
        webSocket?.close(1000, "service stopped")
        serviceScope.cancel()
    }

    private data class PeerState(val rawRssi: Double, val smoothedRssi: Double, val lastSeenAtMs: Long)

    public companion object {
        private const val NOTIFICATION_ID = 1002
        private const val SMOOTHING_ALPHA = 0.3
        private const val LOST_CHECK_INTERVAL_MS = 2000L
        private const val LOST_THRESHOLD_MS = 10_000L

        private val _livePeers = MutableStateFlow<Map<String, Pair<Double, Double>>>(emptyMap())

        /** Live peer RSSI snapshot exposed to the Settings screen. */
        public val livePeers: StateFlow<Map<String, Pair<Double, Double>>> = _livePeers.asStateFlow()
    }
}
