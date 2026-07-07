package com.aether.shared

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/**
 * Wire-format message builders mirroring `aether-bridge/messages.py`.
 *
 * Field names here are a locked contract - do not rename fields without
 * updating every consumer (`aggregator.py`, dashboard). This module only
 * implements the message types an Android scanner produces: `reading` and
 * `lost` (§ PRD.md acceptance criteria - no changes to messages.py/aggregator.py
 * required, since Android is just another producer of the same wire format).
 */
public object Messages {

    private val HMS_FORMATTER: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm:ss")

    private fun nowHms(): String = LocalTime.now().format(HMS_FORMATTER)

    /**
     * Builds the JSON string for a `reading` message, matching
     * `messages.py`'s `build_reading_message` field-for-field:
     * `{"type": "reading", "scanner", "name", "rssi", "smoothedRssi", "lastSeenMs", "ts"}`.
     */
    public fun buildReadingMessage(
        scanner: String,
        name: String,
        rawRssi: Double,
        smoothedRssi: Double,
        lastSeenMs: Long,
    ): String {
        return buildJsonObject {
            put("type", JsonPrimitive("reading"))
            put("scanner", JsonPrimitive(scanner))
            put("name", JsonPrimitive(name))
            put("rssi", JsonPrimitive(rawRssi))
            put("smoothedRssi", JsonPrimitive(smoothedRssi))
            put("lastSeenMs", JsonPrimitive(lastSeenMs))
            put("ts", JsonPrimitive(nowHms()))
        }.toString()
    }

    /**
     * Builds the JSON string for a `lost` message, matching
     * `messages.py`'s `build_lost_message` field-for-field:
     * `{"type": "lost", "scanner", "name", "ts"}`.
     */
    public fun buildLostMessage(scanner: String, name: String): String {
        return buildJsonObject {
            put("type", JsonPrimitive("lost"))
            put("scanner", JsonPrimitive(scanner))
            put("name", JsonPrimitive(name))
            put("ts", JsonPrimitive(nowHms()))
        }.toString()
    }
}
