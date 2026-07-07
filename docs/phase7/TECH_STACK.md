# Tech Stack — Phase 7

- **Language:** Kotlin, Android Gradle Plugin, target/compile SDK 35 (matches the SDK just installed at `D:\Aether-BLE\android-sdk`), min SDK 26 (covers both test phones).
- **Build:** Gradle wrapper (`gradlew`), no system Gradle dependency — checked into `aether-android/`. SDK location via `aether-android/local.properties` (`sdk.dir=D:\\Aether-BLE\\android-sdk`, gitignored) — not a global env var, per project-isolation rule.
- **UI:** Jetpack Compose (Settings screen is the only UI surface this phase — small enough that Compose's lower boilerplate wins over XML views).
- **BLE:** platform `android.bluetooth.le` APIs directly (`BluetoothLeAdvertiser`, `BluetoothLeScanner`) — no third-party BLE library needed for this scope.
- **QR scanning:** CameraX + ML Kit barcode scanning (Google's own stack, no external QR library, avoids a ZXing dependency).
- **Networking:** OkHttp WebSocket client (for streaming `reading`/`lost` messages to the aggregator) + a plain `java.net.Socket` for the Phase-6 pairing TCP handshake (small, custom protocol — no need for a full HTTP client there).
- **Crypto:** Kotlin's `javax.crypto` (`Mac.getInstance("HmacSHA256")`) for beacon HMAC, plus Google's Tink for the Ed25519 pairing key exchange — pick one library, don't build custom Ed25519.
- **Module layout:** `aether-android/shared/` (beacon payload build, HMAC, pairing client logic — pure Kotlin, no Android framework dependency, so it's unit-testable on the JVM without an emulator) + `aether-android/app/` (foreground services, Compose UI, CameraX).
- **Testing:** JUnit + the cross-language beacon test vector from PRD.md, runnable via `gradlew :shared:test` — no emulator required for the crypto/wire-format logic, which is the part most likely to have subtle bugs (endianness, truncation).

Rationale: every choice either reuses what Phase 6 already defined (wire format, message JSON shape) or picks the platform-native option over a third-party library, keeping the dependency surface small for a solo-maintained project.
