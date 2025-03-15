import asyncio
from __init__ import Lamp

import sys

async def main():
    lamp = Lamp("F0:A9:51:9C:4E:96")
    await lamp.connect()
    if sys.argv[1] == "True":
        try:
            await lamp.set_power(True)
        finally:
            await lamp.disconnect()
    elif sys.argv[1] == "False":
        try:
            await lamp.set_power(False)
        finally:
            await lamp.disconnect()
    else:
        try:
            await lamp.set_power(True)
            await lamp.set_brightness(float(sys.argv[1]))
        finally:
            await lamp.disconnect()

asyncio.run(main())
