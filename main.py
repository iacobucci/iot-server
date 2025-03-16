import asyncio
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from contextlib import asynccontextmanager

# Import the Lamp class from your init file
from libhueble import Lamp

# Global lamp instance
lamp = None

# Lamp connection manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    global lamp
    # Initialize lamp and connect on startup
    lamp = Lamp("F0:A9:51:9C:4E:96")
    try:
        await lamp.connect()
        print("Connected to lamp")
        yield
    finally:
        # Disconnect when shutting down
        if lamp:
            await lamp.disconnect()
            print("Disconnected from lamp")

# Initialize FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

# Define request models
class PowerRequest(BaseModel):
    power: bool = Field(..., description="Turn lamp on (true) or off (false)")

class BrightnessRequest(BaseModel):
    brightness: float = Field(..., description="Brightness level between 0.0 and 1.0", ge=0.0, le=1.0)

# Status model for responses
class LampStatus(BaseModel):
    connected: bool
    power: Optional[bool] = None
    brightness: Optional[float] = None

# Routes
@app.get("/status", response_model=LampStatus)
async def get_status():
    """Get the current status of the lamp."""
    global lamp
    if not lamp:
        return LampStatus(connected=False)
    return LampStatus(connected=True, power=lamp.get_power(), brightness=lamp.get_brightness())

@app.get("/power", response_model=LampStatus)
async def set_power(request: PowerRequest):
    """Turn the lamp on or off."""
    global lamp
    if not lamp:
        raise HTTPException(status_code=503, detail="Lamp not connected")
    
    try:
        await lamp.set_power(request.power)
        return LampStatus(connected=True, power=request.power, brightness=lamp.get_brightness())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set power: {str(e)}")

@app.get("/brightness", response_model=LampStatus)
async def set_brightness(request: BrightnessRequest):
    """Set the brightness of the lamp."""
    global lamp
    if not lamp:
        raise HTTPException(status_code=503, detail="Lamp not connected")
    
    try:
        # Ensure lamp is on before setting brightness
        if not lamp.get_power():
            await lamp.set_power(True)
        
        await lamp.set_brightness(request.brightness)
        return LampStatus(connected=True, power=True, brightness=request.brightness)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set brightness: {str(e)}")

# Run the server
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)