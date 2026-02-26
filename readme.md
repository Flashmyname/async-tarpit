# AsyncTarpit

A lightweight TCP tarpit that traps port scanners and wastes their time — without the overhead of a full honeypot.

## Why?

Most honeypots try to emulate entire OS environments or specific services. Scanners fingerprint those instantly, log them, and move on.

AsyncTarpit takes a different approach: it grabs an incoming TCP connection and **refuses to let go**. No dropped connections. No standard banners. It feeds the scanner absolute garbage — one random byte every 10 seconds — dragging out their scans until the tool gives up.

Python's `asyncio` runs the show. Thousands of sockets locked up on a single thread, barely touching RAM or CPU.

## Requirements

- Python 3.7+
- Zero external dependencies

## Usage

```bash
python asynctarpit.py
```

Binds to `0.0.0.0:2222` by default. Edit the config at the top of the script to change the port or delay:

```python
HOST = "0.0.0.0"
PORT = 2222
DELAY = 10  # Seconds between sending bytes
```

## Testing

### Manual

Start the tarpit, then in a second terminal:

```bash
nc -v 127.0.0.1 2222
```

The socket connects immediately. After that — dead silence. Just random characters dropping in every 10 seconds. `Ctrl+C` to disconnect.

### Automated

A test suite is included:

```bash
python test_asynctarpit.py
```

This verifies connection handling, byte drip-feeding, concurrent connections, and clean disconnects — all without needing external tools.

## How It Works

1. Listens on a TCP port (default `2222`)
2. Accepts any incoming connection
3. Sends **1 random byte** every N seconds (default `10`)
4. Never closes the connection — the scanner has to give up first
5. Handles thousands of concurrent connections via `asyncio`

## License

MIT
