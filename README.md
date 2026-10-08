# IoT Server

A FastAPI-based IoT server that runs on a Rock 3C NAS board, bridging the Zigbee protocol (via [zigbee2mqtt](https://github.com/Koenkk/zigbee2mqtt), [mosquitto](https://github.com/eclipse-mosquitto/mosquitto), and a [Sonoff Zigbee dongle plus](https://sonoff.tech/product/gateway-and-sensors/sonoff-zigbee-3-0-usb-dongle-plus-p/)) with:
- **USB Serial Connection**: directly connected to an Arduino Uno R4 dashboard (`workbench-controller`).
- **HTTP REST API**: accessible over the local network from browsers, scripts, and other devices.

## Direct USB Connection (Arduino)

The Arduino board connects directly to the Rock 3C via USB cable (`/dev/ttyACM0` by default at 115200 baud). No WiFi or internet is needed on the Arduino.

### Serial Signals & Protocol

Commands sent by the Arduino (or typed into the monitor):

| Signal / Command | Description | MQTT Action |
|---|---|---|
| `POWER` / `TOGGLE` / `p` | Toggle lamp on/off | `{"state": "ON"/"OFF"}` |
| `BRIGHTNESS_UP` / `+` | Increase brightness | `{"brightness": +51}` |
| `BRIGHTNESS_DOWN` / `-` | Decrease brightness | `{"brightness": -51}` |
| `STATUS` / `?` | Request current status | Replies with `STATUS state=... brightness=...` |
| `ON` / `OFF` | Explicit power on / off | `{"state": "ON"/"OFF"}` |
| `BRIGHTNESS <0.0-1.0>` | Set specific brightness | `{"brightness": value}` |

Whenever the lamp state changes (whether from Arduino USB, HTTP request, or external Zigbee event), the server sends a status update line over USB:
```
STATUS state=ON brightness=204
```

### Using PlatformIO Monitor

You can monitor the serial communication and send test signals using PlatformIO device monitor in two ways:

1. **Directly to Arduino**: When developing with the Arduino plugged into your PC:
   ```bash
   pio device monitor -p /dev/ttyACM0 -b 115200
   ```
2. **Via Virtual Monitor Bridge**: The IoT server creates a virtual pseudo-terminal (PTY) at `/tmp/iot-server-monitor`. While the server is running on the Rock 3C (or locally), you can attach PlatformIO monitor to observe all signals in real time and type commands:
   ```bash
   pio device monitor -p /tmp/iot-server-monitor -b 115200
   ```

### Configuration Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SERIAL_PORT` | `/dev/ttyACM0` | USB device path for Arduino |
| `SERIAL_BAUD` | `115200` | Baud rate for serial communication |
| `ENABLE_VIRTUAL_MONITOR` | `1` | Enable PTY monitor bridge |
| `VIRTUAL_MONITOR_PATH` | `/tmp/iot-server-monitor` | Symlink path for virtual monitor |
| `MQTT_BROKER` | `localhost` | MQTT broker host |
| `MQTT_PORT` | `1883` | MQTT broker port |

## HTTP API

The HTTP server remains fully active for external devices, web dashboard, and automation.

- `GET /status`: Returns JSON status (`{"connected": true, "power": true, "brightness": 0.8}`)
- `POST /power`: Body `{"power": true}`
- `POST /brightness`: Body `{"brightness": 0.5}`
- `GET /increase_brightness` / `GET /decrease_brightness`
- `GET /power?form_toggle=1`: Toggle power (for simple web links/buttons)
- `GET /serial/status`: Returns USB connection and monitor bridge status
- `POST /serial/command`: Send a raw signal command via HTTP (`{"command": "POWER"}`)

## Running the Service

### Systemd Service

```ini
[Unit]
Description=IoT Server
After=network.target

[Service]
WorkingDirectory=/home/valerio/source/py/iot-server
ExecStart=/home/valerio/.local/bin/poetry run python3 main.py
RestartSec=5
Restart=always

[Install]
WantedBy=default.target
```

### Bash Control Script

```bash
#!/bin/bash

URL="rock-3c"
PORT="8000"

case "$1" in
	"status")
		curl "http://$URL:$PORT/status"
		;;
	"0")
		curl "http://$URL:$PORT/power" -X POST -H "Content-Type: application/json" -d '{"power":false}'
		;;
	*)
		curl "http://$URL:$PORT/brightness" -X POST -H "Content-Type: application/json" -d "{\"brightness\":$1}"
		;;
esac

exit $?
```
