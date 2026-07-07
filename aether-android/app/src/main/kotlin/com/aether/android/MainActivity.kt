package com.aether.android

import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.core.content.ContextCompat

public class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            SettingsScreen(
                onStartBeacon = { startForegroundServiceCompat(BeaconService::class.java) },
                onStopBeacon = { stopService(Intent(this, BeaconService::class.java)) },
                onStartScanner = { startForegroundServiceCompat(ScannerService::class.java) },
                onStopScanner = { stopService(Intent(this, ScannerService::class.java)) },
                onStartPairing = { startActivity(Intent(this, PairingActivity::class.java)) },
            )
        }
    }

    private fun startForegroundServiceCompat(serviceClass: Class<*>) {
        val intent = Intent(this, serviceClass)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            ContextCompat.startForegroundService(this, intent)
        } else {
            startService(intent)
        }
    }
}
