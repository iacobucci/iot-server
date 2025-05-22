# Iot server

A simple iot server that runs on my rock-3c NAS, making use of FastAPI to expose the HTTP service and the Zigbee protocol. For it [zigbee2mqtt](https://github.com/Koenkk/zigbee2mqtt), [mosquitto](https://github.com/eclipse-mosquitto/mosquitto) and a [Sonoff Zigbee dongle plus](https://sonoff.tech/product/gateway-and-sensors/sonoff-zigbee-3-0-usb-dongle-plus-p/) are used. As of now i use it only with my one dimmable Philips Hue light.

It can be run with a simple systemd service such as:

```ini
[Unit]
Description=Iot server
After=network.target

[Service]
WorkingDirectory=/home/valerio/source/py/iot-server
ExecStart=/home/valerio/.local/bin/poetry run python3 main.py
RestartSec=5
Restart=always

[Install]
WantedBy=default.target
```

And controlled with a simple bash script:

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

Or with my [launchpad-daemon](https://github.com/iacobucci/launchpad-daemon).
