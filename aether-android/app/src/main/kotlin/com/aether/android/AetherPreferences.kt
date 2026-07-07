package com.aether.android

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "aether_settings")

/**
 * Android-side persistence, semantically equivalent to `aether-bridge`'s
 * `~/.aether/realm.json` (realm key/version - see `realm.py`) and
 * `~/.aether/beacon_counter.json` (per-uid_hash monotonic counter - see
 * `beacon_auth.py`'s `BeaconCounterStore`).
 *
 * DataStore (not SharedPreferences) is used because: it is the current
 * Android-recommended replacement for SharedPreferences, offers a
 * coroutine/Flow-native API matching this app's structured-concurrency
 * design, and avoids SharedPreferences' synchronous-disk-I/O-on-main-thread
 * footguns for a value (the beacon counter) that is written on every beacon
 * rotation.
 */
public class AetherPreferences(private val context: Context) {

    private object Keys {
        val REALM_KEY = stringPreferencesKey("realm_key_hex")
        val REALM_KEY_VERSION = longPreferencesKey("realm_key_version")
        val AGGREGATOR_WS_URL = stringPreferencesKey("aggregator_ws_url")
        val BEACON_NAME = stringPreferencesKey("beacon_name")
        val BEACON_COUNTER_PREFIX = "beacon_counter_"
        val OWN_ED25519_PRIVATE_KEY = stringPreferencesKey("own_ed25519_private_key")
    }

    public val realmKeyHex: Flow<String?> = context.dataStore.data.map { it[Keys.REALM_KEY] }
    public val realmKeyVersion: Flow<Long?> = context.dataStore.data.map { it[Keys.REALM_KEY_VERSION] }
    public val aggregatorWsUrl: Flow<String?> = context.dataStore.data.map { it[Keys.AGGREGATOR_WS_URL] }
    public val beaconName: Flow<String?> = context.dataStore.data.map { it[Keys.BEACON_NAME] }

    /** This device's persisted Ed25519 pairing identity private key (raw 32 bytes, hex-encoded). */
    public val ownEd25519PrivateKeyHex: Flow<String?> =
        context.dataStore.data.map { it[Keys.OWN_ED25519_PRIVATE_KEY] }

    public suspend fun setOwnEd25519PrivateKeyHex(privateKeyHex: String) {
        context.dataStore.edit { it[Keys.OWN_ED25519_PRIVATE_KEY] = privateKeyHex }
    }

    public suspend fun setRealm(realmKeyHex: String, version: Long) {
        context.dataStore.edit {
            it[Keys.REALM_KEY] = realmKeyHex
            it[Keys.REALM_KEY_VERSION] = version
        }
    }

    public suspend fun setAggregatorWsUrl(url: String) {
        context.dataStore.edit { it[Keys.AGGREGATOR_WS_URL] = url }
    }

    public suspend fun setBeaconName(name: String) {
        context.dataStore.edit { it[Keys.BEACON_NAME] = name }
    }

    /**
     * Last-persisted beacon counter for [uidHash], keyed per uid_hash mirroring
     * `BeaconCounterStore`'s `get_last_counter` - defaults to 0 only on true
     * first-run; every subsequent rotation reads back the persisted value so a
     * process/device restart never reopens a replay window (confirmed product
     * decision per beacon_auth.py's docstring).
     */
    public suspend fun getLastCounter(uidHash: Long): Long {
        val key = longPreferencesKey(Keys.BEACON_COUNTER_PREFIX + uidHash)
        return context.dataStore.data.first()[key] ?: 0L
    }

    public suspend fun setLastCounter(uidHash: Long, counter: Long) {
        val key = longPreferencesKey(Keys.BEACON_COUNTER_PREFIX + uidHash)
        context.dataStore.edit { it[key] = counter }
    }
}
