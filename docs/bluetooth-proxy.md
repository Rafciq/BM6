# Giving your BM6 a reliable Bluetooth connection

If your BM6 sensors keep going `unavailable`, this page is for you. It is the
single most common issue with the BM6, and it is almost always about **range**,
not the integration.

## Why the BM6 drops out

The BM6 is a **connectable** BLE device. It does **not** broadcast its readings
in advertisements — Home Assistant has to open an **active GATT connection** to
it every poll, authenticate, read the encrypted values, and disconnect. That is
inherently more fragile than a passive sensor, and it fails often when the only
thing in range is a Bluetooth adapter on the far side of the house.

Two consequences follow:

- **You need a scanner physically near the vehicle.** A car battery monitor seen
  only by a distant host dongle will flap constantly.
- **The scanner must support *active connections*.** This rules out some
  hardware — see below.

This integration (v1.2.0+) already softens the symptom: it holds the last good
reading through short drop-outs instead of blanking the sensors on every miss.
But to actually fix it, add a nearby **Bluetooth proxy**.

## What works, and what doesn't

| Option | Active GATT? | Verdict for BM6 |
| --- | --- | --- |
| ESP32 + ESPHome Bluetooth Proxy | Yes | **Recommended.** Cheap, reliable, first-class in HA. |
| Raspberry Pi running `denvera/bt-proxy` | Yes | Good if you already own a Pi. Small/experimental project. |
| Host USB Bluetooth dongle | Yes | Fine *only* if the host is near the car. |
| **Shelly BLE gateway** | **No** | **Will not work.** Shelly forwards advertisements only; it cannot proxy active connections, regardless of how close it is. |

Home Assistant pools every scanner it has and routes each device's connection
through whichever one sees it strongest with a free connection slot. So you do
**not** configure the BM6 to "use" a proxy — you just add a proxy near the car
and HA moves the BM6 onto it automatically.

---

## Path A — ESP32 + ESPHome (recommended)

### Easiest: ready-made firmware

1. Get any ESP32 dev board (an external-antenna board helps range). For a
   damp/outdoor spot, house it in a small enclosure and power it from a USB
   adapter.
2. Open <https://esphome.io/projects/> (the "Bluetooth Proxy" ready-made
   project) in Chrome/Edge, connect the ESP32 by USB, click install, and enter
   your WiFi. No YAML required.
3. Place it near the car and continue to **Provisioning in Home Assistant**.

### Advanced: your own YAML

Add this in the ESPHome dashboard (Settings for `wifi_ssid`, `wifi_password`,
`api_encryption_key` go in your `secrets.yaml`):

```yaml
substitutions:
  name: bm6-bt-proxy
  friendly_name: BM6 BT Proxy

esphome:
  name: ${name}
  friendly_name: ${friendly_name}
  name_add_mac_suffix: false

esp32:
  board: esp32dev          # change to match your board
  framework:
    type: esp-idf          # esp-idf performs far better than arduino for BT

logger:

api:
  encryption:
    key: !secret api_encryption_key

ota:
  - platform: esphome

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

esp32_ble_tracker:
  scan_parameters:
    active: true

bluetooth_proxy:
  active: true             # THIS enables active GATT connections (required)
  connection_slots: 3      # 3 on WiFi; 4 is safe on Ethernet-based boards
```

> Note: the default scan parameters are recommended — aggressive `interval` /
> `window` values increase CPU/RF load without helping. Keep the ESP32 a few
> metres from routers/switches to avoid interference, and as close to the car as
> practical.

---

## Path B — Raspberry Pi (`denvera/bt-proxy`)

Useful if you already have a spare Pi. It implements the ESPHome native API in
Python, so HA discovers it exactly like an ESP32 proxy, **including active
connections**. It is a small, LLM-assisted project — reliable enough, but run it
under systemd with auto-restart.

Best on a **Pi 4B, wired via Ethernet, with onboard WiFi disabled** so the radio
is dedicated to Bluetooth.

```bash
# On the Pi (Raspberry Pi OS Lite 64-bit), after first boot:
sudo apt update && sudo apt full-upgrade -y
echo "dtoverlay=disable-wifi" | sudo tee -a /boot/firmware/config.txt   # wired only
sudo apt install -y git bluez
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc

sudo mkdir -p /opt/bt-proxy && sudo chown "$USER":"$USER" /opt/bt-proxy
git clone https://github.com/denvera/bt-proxy.git /opt/bt-proxy
cd /opt/bt-proxy && uv sync
sudo usermod -aG bluetooth "$USER"     # re-login after this
sudo reboot
```

Run it as a service (`/etc/systemd/system/bt-proxy.service`):

```ini
[Unit]
Description=Bluetooth Proxy (ESPHome-compatible)
After=network-online.target bluetooth.service
Wants=network-online.target
Requires=bluetooth.service

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/bt-proxy
ExecStart=/home/pi/.local/bin/uv run python -m bt_proxy \
  --name btproxy-bedroom \
  --friendly-name "Bedroom BT Proxy" \
  --adapter hci0 \
  --max-connections 4 \
  --log-level INFO
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now bt-proxy
journalctl -u bt-proxy -f
```

> The Pi proxy uses the ESPHome native API **plaintext** variant — when HA adds
> it, leave the encryption key blank. Plaintext is fine on a trusted LAN; don't
> expose port 6053 to the internet.

---

## Provisioning in Home Assistant

1. **Settings → Devices & Services.** A new **ESPHome** device should be
   discovered (your proxy's friendly name). Click **Configure / Add**.
   - ESP32 with an encryption key: paste the key.
   - Pi `bt-proxy`: leave the key **blank** (plaintext API).
2. If it isn't auto-discovered: **Add Integration → ESPHome →** host = the
   proxy's IP, port `6053`.
3. Once added, HA's **Bluetooth** integration registers it as a remote scanner
   automatically (check **Settings → Devices & Services → Bluetooth**).

## Verifying

- Open the BM6 device. The `..._bluetooth_scanner` sensor should switch to your
  new proxy, and the RSSI should improve dramatically.
- The voltage / temperature / percent sensors should stop flapping to
  `unavailable`.

## Still flaky?

- Move the host USB dongle away from any WiFi access point (2.4 GHz interference
  desensitises BLE — give it 0.5–1 m).
- Move the proxy closer to the car / higher with better line of sight.
- On a Pi, add a nightly `systemctl restart bt-proxy` as insurance against
  long-uptime BLE stalls.
- As a last resort, a £6 ESP32 on the same windowsill is a drop-in replacement —
  nothing else in your setup changes.
