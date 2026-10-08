#!/usr/bin/env python3
import glob
import json
import os
import pty
import select
import sys
import time
from threading import Lock, Thread, Event
from typing import Optional

import paho.mqtt.client as mqtt
import serial
import serial.tools.list_ports
from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# MQTT configuration
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TOPIC_SET = os.getenv("MQTT_TOPIC_SET", "zigbee2mqtt/room/set")
TOPIC_STATE = os.getenv("MQTT_TOPIC_STATE", "zigbee2mqtt/room")

# Serial / USB configuration
SERIAL_PORT = os.getenv("SERIAL_PORT", "/dev/ttyACM0")
SERIAL_BAUD = int(os.getenv("SERIAL_BAUD", "115200"))
ENABLE_VIRTUAL_MONITOR = os.getenv("ENABLE_VIRTUAL_MONITOR", "1") == "1"
VIRTUAL_MONITOR_PATH = os.getenv("VIRTUAL_MONITOR_PATH", "/tmp/iot-server-monitor")

# Thread-safe lamp status
lamp_status = {"state": None, "brightness": None}
status_lock = Lock()

# Serial connection state
serial_lock = Lock()
active_serial_conn = {"ser": None, "port": None, "last_command": None, "last_time": None}
virtual_monitor_master_fd: Optional[int] = None
stop_event = Event()


# Request / Response models
class PowerRequest(BaseModel):
	power: bool


class BrightnessRequest(BaseModel):
	brightness: float = Field(..., ge=0.0, le=1.0)


class LampStatus(BaseModel):
	connected: bool
	power: Optional[bool] = None
	brightness: Optional[float] = None


class SerialStatus(BaseModel):
	connected: bool
	port: Optional[str] = None
	baud: int
	virtual_monitor_path: Optional[str] = None
	last_command: Optional[str] = None


class SerialCommandRequest(BaseModel):
	command: str


# MQTT Helper
def get_mqtt_client():
	try:
		from paho.mqtt.enums import CallbackAPIVersion
		return mqtt.Client(CallbackAPIVersion.VERSION2)
	except (ImportError, AttributeError):
		return mqtt.Client()


def publish_mqtt(payload: dict, transition: Optional[float] = None):
	if transition is not None:
		payload["transition"] = transition
	try:
		client = get_mqtt_client()
		client.connect(MQTT_BROKER, MQTT_PORT, 60)
		client.loop_start()
		client.publish(TOPIC_SET, json.dumps(payload))
		client.loop_stop()
		client.disconnect()
	except Exception as e:
		print(f"[MQTT] Publish error: {e}")


def on_connect(client, userdata, flags, rc, properties=None):
	print(f"[MQTT] Connected to broker (code: {rc})")
	client.subscribe(TOPIC_STATE)


def on_message(client, userdata, msg):
	try:
		data = json.loads(msg.payload.decode())
		with status_lock:
			if "state" in data:
				lamp_status["state"] = data["state"]
			if "brightness" in data:
				lamp_status["brightness"] = data["brightness"]
		notify_serial_status()
	except Exception as e:
		print("[MQTT] Decode error:", e)


def start_mqtt_client():
	client = get_mqtt_client()
	client.on_connect = on_connect
	client.on_message = on_message
	while not stop_event.is_set():
		try:
			client.connect(MQTT_BROKER, MQTT_PORT, 60)
			client.loop_forever()
		except Exception as e:
			print(f"[MQTT] Connection failed: {e}. Retrying in 5 seconds...")
			time.sleep(5)


# Lamp Action Functions
def format_status_message() -> str:
	with status_lock:
		state = lamp_status.get("state")
		brightness = lamp_status.get("brightness")
	return f"STATUS state={state} brightness={brightness}\n"


def notify_serial_status():
	msg = format_status_message()
	# Write to physical USB serial if connected
	with serial_lock:
		ser = active_serial_conn.get("ser")
		if ser and ser.is_open:
			try:
				ser.write(msg.encode())
			except Exception as e:
				print(f"[Serial] Failed to write status update: {e}")

	# Broadcast to virtual monitor PTY
	broadcast_to_virtual_monitor(f"[STATUS] {msg.strip()}\r\n")


def action_set_power(power: bool):
	state = "ON" if power else "OFF"
	publish_mqtt({"state": state, "transition": 0.3})
	with status_lock:
		lamp_status["state"] = state
	notify_serial_status()


def action_toggle_power():
	with status_lock:
		current_state = lamp_status.get("state")
	new_power = (current_state != "ON")
	state = "ON" if new_power else "OFF"
	publish_mqtt({"state": state, "transition": 0.3})
	with status_lock:
		lamp_status["state"] = state
	notify_serial_status()


def action_increase_brightness():
	with status_lock:
		old_brightness = lamp_status.get("brightness")
	if old_brightness is not None:
		new_brightness = min(254, int(old_brightness + (255 / 5)))
	else:
		new_brightness = 254
	publish_mqtt({"state": "ON", "brightness": new_brightness, "transition": 0.3})
	with status_lock:
		lamp_status["state"] = "ON"
		lamp_status["brightness"] = new_brightness
	notify_serial_status()


def action_decrease_brightness():
	with status_lock:
		old_brightness = lamp_status.get("brightness")
	if old_brightness is not None:
		new_brightness = max(1, int(old_brightness - (255 / 5)))
	else:
		new_brightness = 0
	publish_mqtt({"state": "ON", "brightness": new_brightness, "transition": 0.3})
	with status_lock:
		lamp_status["state"] = "ON"
		lamp_status["brightness"] = new_brightness
	notify_serial_status()


def action_set_brightness(brightness_float: float):
	brightness = max(1, min(254, int(brightness_float * 254)))
	publish_mqtt({"state": "ON", "brightness": brightness, "transition": 0.3})
	with status_lock:
		lamp_status["state"] = "ON"
		lamp_status["brightness"] = brightness
	notify_serial_status()


# Serial Protocol Command Dispatcher
def process_serial_command(cmd: str, source: str = "Arduino-USB") -> str:
	cmd = cmd.strip()
	if not cmd:
		return ""

	with serial_lock:
		active_serial_conn["last_command"] = cmd
		active_serial_conn["last_time"] = time.time()

	print(f"[{source}] Command received: {cmd}")
	cmd_upper = cmd.upper()

	if cmd_upper in ("POWER", "TOGGLE", "POWER_TOGGLE", "P"):
		action_toggle_power()
		with status_lock:
			state = lamp_status.get("state")
		response = f"OK POWER_TOGGLED (state={state})"
	elif cmd_upper in ("ON", "POWER_ON", "1"):
		action_set_power(True)
		response = "OK POWER_ON"
	elif cmd_upper in ("OFF", "POWER_OFF", "0"):
		action_set_power(False)
		response = "OK POWER_OFF"
	elif cmd_upper in ("BRIGHTNESS_UP", "INCREASE_BRIGHTNESS", "BRIGHTNESS+", "+", "UP"):
		action_increase_brightness()
		with status_lock:
			b = lamp_status.get("brightness")
		response = f"OK BRIGHTNESS_INCREASED (brightness={b})"
	elif cmd_upper in ("BRIGHTNESS_DOWN", "DECREASE_BRIGHTNESS", "BRIGHTNESS-", "-", "DOWN"):
		action_decrease_brightness()
		with status_lock:
			b = lamp_status.get("brightness")
		response = f"OK BRIGHTNESS_DECREASED (brightness={b})"
	elif cmd_upper in ("STATUS", "UPDATE", "GET_STATUS", "?"):
		response = format_status_message().strip()
	elif cmd_upper.startswith("BRIGHTNESS"):
		parts = cmd.split(None, 1)
		if len(parts) > 1:
			try:
				val = float(parts[1])
				if val > 1.0:
					val = val / 254.0
				action_set_brightness(val)
				response = f"OK BRIGHTNESS_SET ({val})"
			except ValueError:
				response = f"ERROR invalid brightness: {parts[1]}"
		else:
			with status_lock:
				b = lamp_status.get("brightness")
			response = f"BRIGHTNESS={b}"
	elif cmd.startswith("{") and cmd.endswith("}"):
		try:
			payload = json.loads(cmd)
			if "power" in payload:
				action_set_power(bool(payload["power"]))
			if "brightness" in payload:
				action_set_brightness(float(payload["brightness"]))
			if "action" in payload:
				act = str(payload["action"]).upper()
				if act in ("POWER", "TOGGLE"):
					action_toggle_power()
				elif act in ("UP", "INCREASE_BRIGHTNESS", "BRIGHTNESS_UP"):
					action_increase_brightness()
				elif act in ("DOWN", "DECREASE_BRIGHTNESS", "BRIGHTNESS_DOWN"):
					action_decrease_brightness()
			response = "OK JSON processed"
		except Exception as e:
			response = f"ERROR JSON: {e}"
	elif cmd_upper.startswith("READY"):
		# Arduino announced reboot/startup
		notify_serial_status()
		response = "OK ARDUINO_READY"
	else:
		response = f"UNKNOWN_COMMAND: {cmd}"

	print(f"[{source}] Result: {response}")
	return response


# Virtual Monitor / PTY Manager
def broadcast_to_virtual_monitor(message: str):
	global virtual_monitor_master_fd
	if virtual_monitor_master_fd is not None:
		try:
			os.write(virtual_monitor_master_fd, message.encode())
		except Exception:
			pass


def virtual_monitor_worker():
	"""
	Creates a pseudo-terminal (pty) symlinked to VIRTUAL_MONITOR_PATH.
	Allows monitoring serial communication with PlatformIO monitor (`make monitor` or `pio device monitor`)
	and sending interactive test signals directly to the IoT server.
	"""
	global virtual_monitor_master_fd
	try:
		import termios

		master_fd, slave_fd = pty.openpty()
		slave_name = os.ttyname(slave_fd)
		virtual_monitor_master_fd = master_fd

		# Disable local ECHO on slave PTY so server output isn't looped back as input
		try:
			attrs = termios.tcgetattr(slave_fd)
			attrs[3] = attrs[3] & ~termios.ECHO
			termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
		except Exception as e:
			print(f"[Virtual Monitor] termios notice: {e}")

		# Create / update symlink to slave pty
		try:
			if os.path.islink(VIRTUAL_MONITOR_PATH) or os.path.exists(VIRTUAL_MONITOR_PATH):
				os.remove(VIRTUAL_MONITOR_PATH)
			os.symlink(slave_name, VIRTUAL_MONITOR_PATH)
			print(f"[Virtual Monitor] Available at: {VIRTUAL_MONITOR_PATH} -> {slave_name}")
			print(f"[Virtual Monitor] Connect with: pio device monitor -p {VIRTUAL_MONITOR_PATH}")
		except Exception as e:
			print(f"[Virtual Monitor] Symlink creation notice: {e} (slave device: {slave_name})")

		# Greeting banner
		welcome = (
			"\r\n========================================\r\n"
			" IoT Server - PlatformIO Monitor Bridge\r\n"
			" Commands: POWER, BRIGHTNESS_UP, BRIGHTNESS_DOWN, STATUS\r\n"
			" Shortcuts: p, +, -, ?\r\n"
			"========================================\r\n"
		)
		os.write(master_fd, welcome.encode())

		input_buffer = ""
		while not stop_event.is_set():
			r, _, _ = select.select([master_fd], [], [], 0.5)
			if master_fd in r:
				try:
					data = os.read(master_fd, 1024).decode(errors="replace")
				except OSError:
					break
				if not data:
					break

				for char in data:
					if char in ("\r", "\n"):
						cmd = input_buffer.strip()
						input_buffer = ""
						if cmd:
							os.write(master_fd, f"\r\n[INPUT] {cmd}\r\n".encode())
							resp = process_serial_command(cmd, source="PlatformIO-Monitor")
							os.write(master_fd, f"[OUTPUT] {resp}\r\n".encode())
					elif char == "\x7f" or char == "\x08":  # Backspace
						if input_buffer:
							input_buffer = input_buffer[:-1]
							os.write(master_fd, b"\b \b")
					else:
						input_buffer += char
						# Echo back character
						os.write(master_fd, char.encode())
	except Exception as e:
		print(f"[Virtual Monitor] Worker stopped: {e}")
	finally:
		if virtual_monitor_master_fd is not None:
			try:
				os.close(virtual_monitor_master_fd)
			except Exception:
				pass
			virtual_monitor_master_fd = None


# Physical USB Serial Worker Thread
def serial_worker():
	last_error_log = 0.0
	while not stop_event.is_set():
		target_port = SERIAL_PORT
		if not os.path.exists(target_port):
			# Auto-detect available ACM or USB serial ports
			candidates = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
			if candidates:
				target_port = candidates[0]

		if not os.path.exists(target_port):
			now = time.time()
			if now - last_error_log > 10.0:
				print(f"[Serial] Waiting for Arduino USB device (target: {SERIAL_PORT})...")
				last_error_log = now
			time.sleep(2)
			continue

		try:
			print(f"[Serial] Connecting to Arduino on {target_port} at {SERIAL_BAUD} baud...")
			with serial.Serial(target_port, SERIAL_BAUD, timeout=1.0) as ser:
				print(f"[Serial] Connected to {target_port}!")
				with serial_lock:
					active_serial_conn["ser"] = ser
					active_serial_conn["port"] = target_port

				# Announce connection and sync initial status
				ser.write(f"CONNECTED\n{format_status_message()}".encode())
				broadcast_to_virtual_monitor(f"[USB] Connected to physical Arduino on {target_port}\r\n")

				while not stop_event.is_set():
					try:
						line_bytes = ser.readline()
						if not line_bytes:
							continue
						line = line_bytes.decode(errors="replace").strip()
						if not line:
							continue

						broadcast_to_virtual_monitor(f"[ARDUINO -> SERVER] {line}\r\n")
						resp = process_serial_command(line, source="Arduino-USB")
						if resp:
							ser.write((resp + "\n").encode())
							broadcast_to_virtual_monitor(f"[SERVER -> ARDUINO] {resp}\r\n")
					except (serial.SerialException, OSError) as e:
						print(f"[Serial] Device error on {target_port}: {e}")
						broadcast_to_virtual_monitor(f"[USB] Disconnected from {target_port}\r\n")
						break
		except Exception as e:
			now = time.time()
			if now - last_error_log > 10.0:
				print(f"[Serial] Failed to connect to {target_port}: {e}")
				last_error_log = now
			time.sleep(2)
		finally:
			with serial_lock:
				active_serial_conn["ser"] = None
				active_serial_conn["port"] = None


# Background Threads Initialization
mqtt_thread = Thread(target=start_mqtt_client, daemon=True)
mqtt_thread.start()

serial_thread = Thread(target=serial_worker, daemon=True)
serial_thread.start()

if ENABLE_VIRTUAL_MONITOR:
	vmon_thread = Thread(target=virtual_monitor_worker, daemon=True)
	vmon_thread.start()


# FastAPI App
app = FastAPI(title="IoT Server", description="FastAPI Zigbee IoT Server with USB Serial and HTTP Control")


@app.get("/status", response_model=LampStatus)
async def get_status():
	with status_lock:
		state = lamp_status.get("state")
		brightness = lamp_status.get("brightness")
	return LampStatus(
		connected=state is not None,
		power=(state == "ON"),
		brightness=(brightness / 254.0 if brightness is not None else None),
	)


@app.post("/power", response_model=LampStatus)
async def set_power(request: PowerRequest):
	action_set_power(request.power)
	time.sleep(0.2)  # Allow state update to propagate
	return await get_status()


@app.post("/brightness", response_model=LampStatus)
async def set_brightness(request: BrightnessRequest):
	action_set_brightness(request.brightness)
	time.sleep(0.2)
	return await get_status()


@app.get("/increase_brightness", response_model=LampStatus)
async def increase_brightness():
	action_increase_brightness()
	time.sleep(0.2)
	return await get_status()


@app.get("/decrease_brightness", response_model=LampStatus)
async def decrease_brightness():
	action_decrease_brightness()
	time.sleep(0.2)
	return await get_status()


@app.get("/power")
async def toggle_power(form_toggle: int = Query(None)):
	if form_toggle == 1:
		action_toggle_power()
		time.sleep(0.2)
	return RedirectResponse(url="/", status_code=302)


@app.get("/brightness")
async def set_brightness_get(brightness: float = Query(0.5)):
	action_set_brightness(brightness)
	time.sleep(0.2)
	return RedirectResponse(url="/", status_code=302)


# Serial Management Endpoints
@app.get("/serial/status", response_model=SerialStatus)
async def get_serial_status():
	with serial_lock:
		ser = active_serial_conn.get("ser")
		port = active_serial_conn.get("port")
		last_cmd = active_serial_conn.get("last_command")
		is_connected = ser is not None and ser.is_open

	return SerialStatus(
		connected=is_connected,
		port=port,
		baud=SERIAL_BAUD,
		virtual_monitor_path=VIRTUAL_MONITOR_PATH if ENABLE_VIRTUAL_MONITOR else None,
		last_command=last_cmd,
	)


@app.post("/serial/command")
async def send_serial_command(request: SerialCommandRequest):
	response = process_serial_command(request.command, source="HTTP-API")
	return {"command": request.command, "response": response}


# Mount static web files
app.mount("/", StaticFiles(directory="public", html=True), name="static")


# Run server
if __name__ == "__main__":
	import uvicorn

	uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
