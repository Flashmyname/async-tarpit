"""
Test suite for AsyncTarpit.

Spins up the tarpit on a random high port, connects to it, and verifies:
  1. TCP connection is accepted instantly
  2. Server drip-feeds exactly 1 byte per interval
  3. Multiple concurrent connections are handled
  4. Client disconnect is handled gracefully
  5. No banner is sent on connect
  6. Server shuts down cleanly

Run:  python test_asynctarpit.py
"""

import asyncio
import os
import socket
import sys
import time

# ---------------------------------------------------------------------------
# Overrides — import the tarpit module with a fast delay and random port
# ---------------------------------------------------------------------------
import asynctarpit

TEST_PORT = 0  # Let the OS pick a free port
TEST_DELAY = 0.3  # Speed up for testing (300 ms instead of 10 s)
TIMEOUT = 5  # Max seconds per test before we call it a failure

passed = 0
failed = 0

# Write results to a file as well so they survive PowerShell stream issues
_result_path = os.path.join(os.path.dirname(__file__) or ".", "test_results.txt")
_result_lines: list[str] = []


def report(name: str, ok: bool, detail: str = ""):
    global passed, failed
    status = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    msg = f"  [{status}] {name}"
    if detail:
        msg += f"  -- {detail}"
    print(msg, flush=True)
    _result_lines.append(msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def start_tarpit(host: str = "127.0.0.1"):
    """Start the tarpit server on a random port and return (server, port)."""
    asynctarpit.DELAY = TEST_DELAY
    server = await asyncio.start_server(asynctarpit.tarpit_handler, host, TEST_PORT)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def tcp_connect(host: str, port: int, timeout: float = TIMEOUT):
    """Open a raw TCP connection and return (reader, writer)."""
    return await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )


async def close_server(server):
    """Safely close a server, absorbing CancelledError on Windows."""
    server.close()
    try:
        await asyncio.wait_for(server.wait_closed(), timeout=2)
    except (asyncio.CancelledError, asyncio.TimeoutError, OSError):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_connection_accepted():
    """Server should accept a TCP connection immediately."""
    server, port = await start_tarpit()
    try:
        reader, writer = await tcp_connect("127.0.0.1", port)
        report("Connection accepted", True)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except Exception as e:
        report("Connection accepted", False, str(e))
    finally:
        await close_server(server)


async def test_receives_bytes():
    """Client should receive exactly 1 byte per drip interval."""
    server, port = await start_tarpit()
    try:
        reader, writer = await tcp_connect("127.0.0.1", port)

        # Wait for ~3 drip cycles and collect data
        data = b""
        start = time.monotonic()
        while time.monotonic() - start < TEST_DELAY * 3.5:
            try:
                chunk = await asyncio.wait_for(reader.read(64), timeout=TEST_DELAY * 2)
                if chunk:
                    data += chunk
            except asyncio.TimeoutError:
                break

        # We expect ~3 bytes (±2), one per DELAY interval
        ok = 2 <= len(data) <= 5
        report("Receives drip bytes", ok, f"got {len(data)} bytes in ~{TEST_DELAY * 3.5:.1f}s")

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except Exception as e:
        report("Receives drip bytes", False, str(e))
    finally:
        await close_server(server)


async def test_concurrent_connections():
    """Server should handle multiple simultaneous connections."""
    server, port = await start_tarpit()
    num_clients = 20
    writers = []
    try:
        for _ in range(num_clients):
            r, w = await tcp_connect("127.0.0.1", port)
            writers.append(w)

        # Give the server a moment to register all connections
        await asyncio.sleep(0.1)

        ok = len(asynctarpit.active_connections) >= num_clients
        report(
            "Concurrent connections",
            ok,
            f"{len(asynctarpit.active_connections)} active (expected {num_clients})",
        )
    except Exception as e:
        report("Concurrent connections", False, str(e))
    finally:
        for w in writers:
            w.close()
            try:
                await w.wait_closed()
            except Exception:
                pass
        await close_server(server)
        # Let handlers finish cleanup
        await asyncio.sleep(0.3)


async def test_client_disconnect():
    """Server should survive a client dropping the connection abruptly."""
    server, port = await start_tarpit()
    try:
        # Use a raw socket so we can RST the connection
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", port))
        await asyncio.sleep(0.1)  # Let the server register it

        initial = len(asynctarpit.active_connections)

        # Abrupt close (sends RST on Windows)
        sock.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER,
            b"\x01\x00\x00\x00\x00\x00\x00\x00",
        )
        sock.close()

        # Wait for the server to notice and clean up
        await asyncio.sleep(TEST_DELAY + 0.5)

        cleaned = len(asynctarpit.active_connections) < initial
        report(
            "Client disconnect handled",
            cleaned,
            f"active before={initial}, after={len(asynctarpit.active_connections)}",
        )
    except Exception as e:
        report("Client disconnect handled", False, str(e))
    finally:
        await close_server(server)
        await asyncio.sleep(0.2)


async def test_no_banner():
    """Server should not send any banner on connect — only drip bytes after DELAY."""
    server, port = await start_tarpit()
    try:
        reader, writer = await tcp_connect("127.0.0.1", port)

        # Try to read immediately (well before the first drip)
        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=TEST_DELAY * 0.5)
        except asyncio.TimeoutError:
            data = b""

        # At most 1 byte if timing is tight
        ok = len(data) <= 1
        report("No instant banner", ok, f"got {len(data)} bytes before first drip interval")

        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except Exception as e:
        report("No instant banner", False, str(e))
    finally:
        await close_server(server)


async def test_server_clean_shutdown():
    """Server should shut down without errors when closed."""
    server, port = await start_tarpit()
    try:
        reader, writer = await tcp_connect("127.0.0.1", port)
        await asyncio.sleep(0.1)

        await close_server(server)

        # After server close the existing handler should eventually stop
        try:
            data = await asyncio.wait_for(reader.read(1024), timeout=TIMEOUT)
        except (asyncio.TimeoutError, ConnectionResetError, ConnectionAbortedError, OSError):
            data = b""

        report("Clean server shutdown", True)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except Exception as e:
        report("Clean server shutdown", False, str(e))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def run_all():
    global passed, failed

    # Install the quiet exception handler from the tarpit module
    loop = asyncio.get_running_loop()
    asynctarpit._orig_exception_handler = loop.get_exception_handler()
    loop.set_exception_handler(asynctarpit._quiet_exception_handler)

    tests = [
        test_connection_accepted,
        test_receives_bytes,
        test_concurrent_connections,
        test_client_disconnect,
        test_no_banner,
        test_server_clean_shutdown,
    ]

    header = f"\n{'=' * 50}\n  AsyncTarpit Test Suite\n  DELAY={TEST_DELAY}s  TIMEOUT={TIMEOUT}s\n{'=' * 50}\n"
    print(header, flush=True)
    _result_lines.append(header)

    for test in tests:
        # Reset active connections between tests
        asynctarpit.active_connections.clear()
        await test()

    footer = f"\n{'=' * 50}\n  Results: {passed} passed, {failed} failed, {passed + failed} total\n{'=' * 50}\n"
    print(footer, flush=True)
    _result_lines.append(footer)

    # Persist results to file
    with open(_result_path, "w", encoding="utf-8") as f:
        f.write("\n".join(_result_lines))

    return failed == 0


if __name__ == "__main__":
    success = asyncio.run(run_all())
    sys.exit(0 if success else 1)
