# Changelog

<!--next-version-placeholder-->

## [Unreleased]
- Added stored-history download: the integration now downloads the voltage, state of charge and temperature records kept on the BM6 device (one record every 2 minutes) and imports them into Home Assistant long-term statistics. Configurable with the new "Download stored history" and "Stored history pages" options, and triggerable on demand with the new `bm6.download_history` service. Protocol reverse-engineered by [marcomc/BMGateway](https://github.com/marcomc/BMGateway).
- Two-minute history records are now also written as recorder state rows after every import, so the history graphs show the full 2-minute resolution for the recent period (long-term statistics are hourly-only). The raw records are also kept in `bm6_history_<address>.json` under the config directory as a permanent archive.
- Every import now fills gaps from that JSON archive: missing hourly statistics rows and missing 2-minute state rows are backfilled on every cycle, even when the download returns no new records or the device cannot be reached at all.
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