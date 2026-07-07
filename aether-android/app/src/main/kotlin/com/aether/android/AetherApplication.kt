package com.aether.android

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build

public class AetherApplication : Application() {

    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return

        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                BEACON_CHANNEL_ID,
                "Aether Beacon",
                NotificationManager.IMPORTANCE_LOW,
            ),
        )
        manager.createNotificationChannel(
            NotificationChannel(
                SCANNER_CHANNEL_ID,
                "Aether Scanner",
                NotificationManager.IMPORTANCE_LOW,
            ),
        )
    }

    public companion object {
        public const val BEACON_CHANNEL_ID: String = "aether_beacon"
        public const val SCANNER_CHANNEL_ID: String = "aether_scanner"
    }
}
