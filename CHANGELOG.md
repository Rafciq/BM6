# Changelog

<!--next-version-placeholder-->

## 1.0.7
- Fixed sensors showing 0 values since 1.0.6: right after subscribing the BM6 sends an empty all-zero frame, which is now ignored. If only that frame arrives, the request is sent once more.

## 1.0.6
- Subscribe to notifications before sending the request, so a fast reply from the BM6 is no longer lost (read timed out although the device answered).
- `stop_notify` and `disconnect` are now bounded by a timeout and always run, so a hung cleanup no longer blocks the update or keeps a Bluetooth proxy connection slot occupied.
- Bluetooth discovery only matches known BM6 manufacturer signatures (Rafciq/BM6#45), revised German translation (Rafciq/BM6#41), local brand icons (Rafciq/BM6#46).

## 1.0.5
- Connections are now established with `bleak_retry_connector.establish_connection()` instead of a bare `BleakClient`. Aborted connection attempts are cleaned up on the ESPHome Bluetooth proxy, which otherwise ended up in `ESP_GATT_ERROR` and stopped scanning until restarted.
- Fixed scanner fallback: the device is no longer read via every scanner on success, and the next scanner is tried when one fails.

## 1.0.4
- Fixed BM6 devices no longer updating until the Bluetooth proxy was restarted. Waiting for the device response now times out, so a lost notification no longer keeps the connection (and a connection slot of an ESPHome Bluetooth proxy) open forever. The connection is released and retried on the next update.

## 1.0.3
- Added support for multiple Bluetooth scanners/gateways. Now the BM6 device is supported by more than one Bluetooth scanner/gateway. The scanner with the best signal strength is automatically selected to connect to the BM6. If you have more than one scanner/gateway, this version is just for you.
## 1.0.2
- Improvement of code especially translation.
## 1.0.1
- Improvement of code especially translation.
## 1.0.0
- First release.