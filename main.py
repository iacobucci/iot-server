#!/usr/bin/env python3
import asyncio
import json
from threading import Thread
from typing import Optional

import paho.mqtt.client as mqtt
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from fastapi import Query
from fastapi.responses import RedirectResponse

# MQTT config
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
TOPIC_SET = "zigbee2mqtt/room/set"
TOPIC_STATE = "zigbee2mqtt/room"

# Async-safe bridge for latest lamp status
lamp_status = {"state": None, "brightness": None}
status_lock = asyncio.Lock()


# Request models
class PowerRequest(BaseModel):
    power: bool


class BrightnessRequest(BaseModel):
    brightness: float = Field(..., ge=0.0, le=1.0)


class LampStatus(BaseModel):
    connected: bool
    power: Optional[bool] = None
    brightness: Optional[float] = None


# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    print("MQTT connected")
    client.subscribe(TOPIC_STATE)


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        asyncio.run(update_lamp_status(data))
    except Exception as e:
        print("MQTT decode error:", e)


async def update_lamp_status(data):
    async with status_lock:
        if "state" in data:
            lamp_status["state"] = data["state"]
        if "brightness" in data:
            lamp_status["brightness"] = data["brightness"]


# Start MQTT client in a separate thread
def start_mqtt_client():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_forever()


mqtt_thread = Thread(target=start_mqtt_client, daemon=True)
mqtt_thread.start()

# FastAPI app
app = FastAPI()

@app.get("/status", response_model=LampStatus)
async def get_status():
    async with status_lock:
        state = lamp_status.get("state")
        brightness = lamp_status.get("brightness")
    return LampStatus(
        connected=state is not None,
        power=(state == "ON"),
        brightness=(brightness / 254.0 if brightness is not None else None),
    )


@app.post("/power", response_model=LampStatus)
async def set_power(request: PowerRequest):
    state = "ON" if request.power else "OFF"
    publish_mqtt({"state": state, "transition": 0.3})
    await asyncio.sleep(0.2)  # Allow state update to propagate
    return await get_status()


@app.post("/brightness", response_model=LampStatus)
async def set_brightness(request: BrightnessRequest):
    brightness = int(request.brightness * 254)
    publish_mqtt({"state": "ON", "brightness": brightness, "transition": 0.3})
    await asyncio.sleep(0.2)
    return await get_status()

@app.get("/increase_brightness", response_model=LampStatus)
async def increase_brightness():
    publish_mqtt({"state": "ON", "brightness": lamp_status["brightness"] + 255/10, "transition": 0.3})
    await asyncio.sleep(0.2)
    return await get_status()

@app.get("/decrease_brightness", response_model=LampStatus)
async def increase_brightness():
    publish_mqtt({"state": "ON", "brightness": lamp_status["brightness"] - 255/10, "transition": 0.3})
    await asyncio.sleep(0.2)
    return await get_status()

@app.get("/power")
async def toggle_power(form_toggle: int = Query(None)):
    if form_toggle == 1:
        # Ottieni lo stato attuale per fare il toggle
        current_status = await get_status()
        new_power = not current_status.power
        
        state = "ON" if new_power else "OFF"
        publish_mqtt({"state": state, "transition": 0.3})
        await asyncio.sleep(0.2)
    
    return RedirectResponse(url="/", status_code=302)

@app.get("/brightness")
async def set_brightness_get(brightness: float = Query(0.5)):
    brightness_value = int(brightness * 254)
    publish_mqtt({"state": "ON", "brightness": brightness_value, "transition": 0.3})
    await asyncio.sleep(0.2)
    
    return RedirectResponse(url="/", status_code=302)

app.mount("/", StaticFiles(directory="public", html=True), name="static")


def publish_mqtt(payload: dict, transition: Optional[float] = None):
    if transition is not None:
        payload["transition"] = transition
    client = mqtt.Client()
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    client.publish(TOPIC_SET, json.dumps(payload))
    client.loop_stop()
    client.disconnect()


# Run the server
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
