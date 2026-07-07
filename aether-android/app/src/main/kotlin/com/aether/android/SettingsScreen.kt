package com.aether.android

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Divider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

/**
 * Settings screen (Compose): paired status, realm key version, live
 * scanned-peer RSSI table - per PRD.md's settings-screen acceptance
 * criteria. This is the only UI surface built this phase.
 */
@Composable
public fun SettingsScreen(
    onStartBeacon: () -> Unit,
    onStopBeacon: () -> Unit,
    onStartScanner: () -> Unit,
    onStopScanner: () -> Unit,
    onStartPairing: () -> Unit,
) {
    val context = LocalContext.current
    val preferences = remember { AetherPreferences(context) }
    val coroutineScope = rememberCoroutineScope()

    val realmKeyVersion by preferences.realmKeyVersion.collectAsState(initial = null)
    val isPaired = realmKeyVersion != null
    val livePeers by ScannerService.livePeers.collectAsState()

    var aggregatorUrlInput by remember { mutableStateOf("") }
    val savedAggregatorUrl by preferences.aggregatorWsUrl.collectAsState(initial = null)

    Scaffold { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text("Aether", style = MaterialTheme.typography.headlineMedium)

            Text("Paired: ${if (isPaired) "Yes" else "No"}")
            Text("Realm key version: ${realmKeyVersion ?: "—"}")

            Button(onClick = onStartPairing) { Text("Scan pairing QR") }

            Divider()

            Text("Aggregator WebSocket address", style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(
                value = aggregatorUrlInput,
                onValueChange = { aggregatorUrlInput = it },
                placeholder = { Text(savedAggregatorUrl ?: "ws://192.168.1.10:8765") },
                modifier = Modifier.fillMaxWidth(),
            )
            Button(onClick = {
                coroutineScope.launch { preferences.setAggregatorWsUrl(aggregatorUrlInput) }
            }) {
                Text("Save aggregator address")
            }

            Divider()

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onStartBeacon) { Text("Start beacon") }
                Button(onClick = onStopBeacon) { Text("Stop beacon") }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = onStartScanner) { Text("Start scanner") }
                Button(onClick = onStopScanner) { Text("Stop scanner") }
            }

            Divider()

            Text("Live scanned peers", style = MaterialTheme.typography.titleMedium)
            LazyColumn(modifier = Modifier.fillMaxWidth()) {
                items(livePeers.entries.toList()) { (name, rssi) ->
                    val (raw, smoothed) = rssi
                    Row(
                        modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text(name)
                        Text("raw: ${"%.1f".format(raw)} dBm  smoothed: ${"%.1f".format(smoothed)} dBm")
                    }
                }
            }
        }
    }
}
