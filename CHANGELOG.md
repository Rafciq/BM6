# Changelog

<!--next-version-placeholder-->

## [Unreleased]
- Added stored-history download: the integration now downloads the voltage, state of charge and temperature records kept on the BM6 device (one record every 2 minutes) and imports them into Home Assistant long-term statistics. Configurable with the new "Download stored history" and "Stored history pages" options, and triggerable on demand with the new `bm6.download_history` service. Protocol reverse-engineered by [marcomc/BMGateway](https://github.com/marcomc/BMGateway).
- Voltage sensor now shows 2 decimal places and the percentage sensor now reports its unit as %.
## 1.0.3
- Added support for multiple Bluetooth scanners/gateways. Now the BM6 device is supported by more than one Bluetooth scanner/gateway. The scanner with the best signal strength is automatically selected to connect to the BM6. If you have more than one scanner/gateway, this version is just for you.
## 1.0.2
- Improvement of code especially translation.
## 1.0.1
- Improvement of code especially translation.
## 1.0.0
- First release.