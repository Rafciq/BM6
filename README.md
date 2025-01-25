# Battery Monitor BM6 integration
[![Version](https://img.shields.io/badge/version-1.0.0-green.svg?style=for-the-badge)](#) [![maintained](https://img.shields.io/maintenance/yes/2025.svg?style=for-the-badge)](#) [![maintainer](https://img.shields.io/badge/maintainer-Rafal%20Drzymala%20%40Rafciq-blue.svg?style=for-the-badge)](#)

[![GitHub release (latest by date)][release-badge]][release-link] [![GitHub][license-badge]][license-link] [![hacs_badge][hacs-badge]][hacs-link] [![GitHub stars][stars-badge]][stars-link] ![GitHub][maintained-badge] [![GitHub issues][issues-badge]][issues-link] [![GitHub commits since latest release (by SemVer)][commits-badge]][commits-link]

This custom component for [Home Assistant](https://www.home-assistant.io) reads BLE Car Battery Monitor BM6.

**:warning: IMPORTANT! Coming soon available**

## Integration
This is an integration that allows you to observe BM6 parameters on the Home Assistant platform. BM6 is monitored via a Bluetooth gateway, and its parameters such as temperature and voltage are saved as entities by the HA platform.

The integration allows you to add any number of such sensors.

## Device
The Battery Monitor BM6 is a device designed to help you keep track of your car battery's health and performance. Here are some key features:
- **Real-time Voltage Monitoring**: It allows you to monitor the voltage of your car battery in real-time.
- **Battery Testing**: You can test the starting and charging system voltage to ensure your battery is functioning properly.
- **Data Logging**: The device can accurately record the time of car starting and stopping, and all data can be displayed on your mobile phone via Bluetooth.
- **Compatibility**: The BM6 is compatible with most 12V car batteries and can be easily installed in your vehicle.
- **Bluetooth Connectivity**: It connects to your smartphone via Bluetooth, allowing you to monitor your battery's health and performance in real-time.

Battery Monitor BM6 is also available under other names:
- Sealey BT2020 Battery Monitor
- ANCEL BM200 Car Battery Tester
- QUICKLYNKS Battery Monitor BM6

## Installation

Search for and install `bm6` from [HACS](https://hacs.xyz/).

## Configuration

This integration can **only** be configured via the UI.

## Inspiration and resources
This project is inspired and based on the hard work of other people and their publications:
- https://www.tarball.ca/posts/reverse-engineering-the-bm6-ble-battery-monitor/
- https://github.com/jeffwdh/bm6-battery-monitor
- https://github.com/KrystianD/bm2-battery-monitor/blob/master/.docs/reverse_engineering.md
- https://doubleagent.net/bm2-reversing-the-ble-protocol-of-the-bm2-battery-monitor/

## Image
![Image](https://github.com/user-attachments/assets/ff37c593-1b24-445f-94bc-d7814403de69)