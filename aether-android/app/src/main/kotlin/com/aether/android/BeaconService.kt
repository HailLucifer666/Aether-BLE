package com.aether.android

import android.app.Notification
import android.app.Service
import android.bluetooth.BluetoothManager
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.bluetooth.le.BluetoothLeAdvertiser
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.aether.shared.BeaconAuth
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.launch

/**
 * Foreground service that advertises the authenticated Phase 6 beacon
 * wire format via [BluetoothLeAdvertiser], manufacturer-data ID [MANUFACTURER_ID].
 *
 * The rotating counter is persisted via [AetherPreferences] (DataStore) so a
 * process/device restart does not reopen a replay window at counter=0 - the
 * same anti-replay requirement `beacon_auth.py`'s `BeaconCounterStore`
 * enforces server-side (see ARCHITECTURE.md data flow - beacon).
 */
public class BeaconService : Service() {

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var advertisingJob: Job? = null
    private var advertiser: BluetoothLeAdvertiser? = null
    private lateinit var preferences: AetherPreferences

    override fun onCreate() {
        super.onCreate()
        preferences = AetherPreferences(applicationContext)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForegroundCompat()

        val adapter = (getSystemService(BluetoothManager::class.java))?.adapter
        advertiser = adapter?.bluetoothLeAdvertiser

        if (advertisingJob?.isActive != true) {
            advertisingJob = serviceScope.launch { rotateBeaconLoop() }
        }

        return START_STICKY
    }

    private suspend fun rotateBeaconLoop() {
        while (true) {
            rotateAndAdvertiseOnce()
            delay(ROTATION_INTERVAL_MS)
        }
    }

    private suspend fun rotateAndAdvertiseOnce() {
        val realmKeyHexValue = preferences.realmKeyHex.firstOrNull() ?: return
        val beaconName = preferences.beaconName.firstOrNull() ?: DEFAULT_BEACON_NAME

        val realmKey = hexToBytes(realmKeyHexValue)
        val uidHash = BeaconAuth.uidHashFromName(beaconName)
        val lastCounter = preferences.getLastCounter(uidHash)
        val nextCounter = lastCounter + 1

        val payload = BeaconAuth.buildBeaconPayload(realmKey, uidHash, nextCounter)
        preferences.setLastCounter(uidHash, nextCounter)

        startAdvertising(payload)
    }

    private fun startAdvertising(payload: ByteArray) {
        val advertiser = this.advertiser ?: return

        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
            .setConnectable(false)
            .build()

        val data = AdvertiseData.Builder()
            .addManufacturerData(MANUFACTURER_ID, payload)
            .setIncludeDeviceName(false)
            .build()

        try {
            advertiser.stopAdvertising(advertiseCallback)
            advertiser.startAdvertising(settings, data, advertiseCallback)
        } catch (e: SecurityException) {
            // BLUETOOTH_ADVERTISE not granted; nothing to advertise until the
            // user grants it from Settings. Foreground service stays alive so
            // the user sees the persistent notification and can act on it.
        }
    }

    private val advertiseCallback = object : AdvertiseCallback() {
        override fun onStartFailure(errorCode: Int) {
            // Advertising failed to start (e.g. too many concurrent advertisers,
            // or BT off) - the next rotation tick will retry.
        }
    }

    private fun startForegroundCompat() {
        val notification: Notification = NotificationCompat.Builder(this, AetherApplication.BEACON_CHANNEL_ID)
            .setContentTitle("Aether beacon active")
            .setContentText("Advertising presence to the realm")
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
        advertiser?.stopAdvertising(advertiseCallback)
        serviceScope.cancel()
    }

    private fun hexToBytes(hex: String): ByteArray {
        val clean = hex.trim()
        val out = ByteArray(clean.length / 2)
        for (i in out.indices) {
            val index = i * 2
            out[i] = ((Character.digit(clean[index], 16) shl 4) + Character.digit(clean[index + 1], 16)).toByte()
        }
        return out
    }

    public companion object {
        public const val MANUFACTURER_ID: Int = 0xFFFF
        public const val ROTATION_INTERVAL_MS: Long = 5000L
        public const val DEFAULT_BEACON_NAME: String = "aether-node"
        private const val NOTIFICATION_ID = 1001
    }
}
