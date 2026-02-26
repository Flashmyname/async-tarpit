import asyncio
import logging
import os

# Conf
HOST = "0.0.0.0"
PORT = 2222
DELAY = 10  # Seconds between sending bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

active_connections: set = set()

# Windows Proactor event loop fires internal transport callbacks that raise
# ConnectionAbortedError on abrupt disconnects. These get logged by asyncio
# *before* the exception reaches user-level awaits.  Suppress them here so
# they don't clutter output.
_SUPPRESSED = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)

_orig_exception_handler = None


def _quiet_exception_handler(loop, context):
    exc = context.get("exception")
    if isinstance(exc, _SUPPRESSED):
        return  # swallow known transport-level noise
    if _orig_exception_handler:
        _orig_exception_handler(loop, context)
    else:
        loop.default_exception_handler(context)


async def tarpit_handler(reader, writer):
    peername = writer.get_extra_info("peername")
    client_ip, client_port = peername if peername else ("unknown", 0)
    logging.info(f"Connection trapped: {client_ip}:{client_port}")
    active_connections.add(writer)

    try:
        while True:
            # Send 1 random byte to prevent client-side timeout
            writer.write(os.urandom(1))
            await writer.drain()
            await asyncio.sleep(DELAY)
    except _SUPPRESSED:
        logging.info(f"Connection lost: {client_ip}")
    except Exception as e:
        logging.error(f"Error ({client_ip}): {e}")
    finally:
        active_connections.discard(writer)
        if not writer.is_closing():
            writer.close()
        try:
            await writer.wait_closed()
        except (*_SUPPRESSED, OSError):
            pass
        logging.info(f"Connection closed: {client_ip}:{client_port}")


async def main():
    loop = asyncio.get_running_loop()
    global _orig_exception_handler
    _orig_exception_handler = loop.get_exception_handler()
    loop.set_exception_handler(_quiet_exception_handler)

    server = await asyncio.start_server(tarpit_handler, HOST, PORT)
    logging.info(f"Async Tarpit active on {HOST}:{PORT}. Waiting for scanners...")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info(f"Tarpit stopped. {len(active_connections)} connections were active.")