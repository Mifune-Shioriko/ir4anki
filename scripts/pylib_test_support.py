"""Keep loop timers active in restricted runners with delayed thread wakeups."""
import asyncio
from contextlib import suppress

def run_alive(coro):
    async def wrapper():
        async def heartbeat():
            while True: await asyncio.sleep(.05)
        task=asyncio.create_task(heartbeat())
        try: return await coro
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError): await task
    return asyncio.run(wrapper())
