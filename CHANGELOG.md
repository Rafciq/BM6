# Changelog

<!--next-version-placeholder-->

## [Unreleased]
- Added stored-history download: the integration now downloads the voltage, state of charge and temperature records kept on the BM6 device (one record every 2 minutes) and imports them as two-minute state rows, so the history graphs show the raw values exactly as recorded. Configurable with the new "Download stored history" and "Stored history pages" options, and triggerable on demand with the new `bm6.download_history` service. Protocol reverse-engineered by [marcomc/BMGateway](https://github.com/marcomc/BMGateway).
- The voltage, percentage and temperature sensors no longer carry a state class, so Home Assistant shows the raw two-minute values in the history graph instead of a five-minute aggregated (min/mean/max) statistics view. No hourly or short-term statistics are written for these sensors anymore; the raw records live in the state rows and in the permanent `bm6_history_<address>.json` archive.
- Every import now fills gaps from that JSON archive: missing 2-minute state rows are backfilled on every cycle, even when the download returns no new records or the device cannot be reached at all.
- If a live poll fails after retries, the sensors now keep showing the last known value (stale) instead of going "Unavailable"; the history import backfills the gap from the device's stored records once it is reachable again.
- The live poll is retried up to three times (5-second pause between attempts) on a transient Bluetooth failure, so a single missed connect no longer leaves the sensors unavailable until the next hourly poll.
- Voltage sensor now shows 2 decimal places and the percentage sensor now reports its unit as %.
- Renamed the voltage sensor from "Tension" to "Voltage" in the English translation (the French word for voltage had leaked into the English UI).
## 1.0.3
- Added support for multiple Bluetooth scanners/gateways. Now the BM6 device is supported by more than one Bluetooth scanner/gateway. The scanner with the best signal strength is automatically selected to connect to the BM6. If you have more than one scanner/gateway, this version is just for you.
## 1.0.2
- Improvement of code especially translation.
## 1.0.1
- Improvement of code especially translation.
## 1.0.0
- First release.