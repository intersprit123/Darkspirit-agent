"""Start a localhost-only, observe-only WebSocket bridge."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

HOST = "127.0.0.1"
PORT = 17613
TOKEN_FILE = Path.home() / ".darkspirit-agent" / "bridge.token"
MAX_MESSAGE_BYTES = 16_384


def load_token() -> str:
    """Load or create a random local bridge token."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(TOKEN_FILE.parent, 0o700)
    except OSError:
        pass
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(token) >= 32:
            return token
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return token


async def handler(websocket) -> None:
    """Authenticate a client and accept status messages only; never run actions."""
    token = load_token()
    try:
        first = await asyncio.wait_for(websocket.recv(), timeout=10)
        if not isinstance(first, str) or len(first) > MAX_MESSAGE_BYTES:
            await websocket.close(code=1008, reason="Invalid authentication message")
            return
        try:
            auth = json.loads(first)
        except json.JSONDecodeError:
            await websocket.close(code=1008, reason="Expected JSON authentication")
            return
        if not isinstance(auth, dict) or auth.get("type") != "authenticate" or not secrets.compare_digest(str(auth.get("token", "")), token):
            await websocket.close(code=1008, reason="Authentication failed")
            return
        await websocket.send(json.dumps({"type": "ready", "mode": "observe-only", "actions_enabled": False}))
        async for message in websocket:
            if not isinstance(message, str) or len(message) > MAX_MESSAGE_BYTES:
                await websocket.close(code=1009, reason="Message too large")
                return
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type": "error", "error": "invalid_json"}))
                continue
            # Intentionally do not dispatch commands to Windows-Use yet.
            if isinstance(payload, dict) and payload.get("type") == "ping":
                await websocket.send(json.dumps({"type": "pong", "mode": "observe-only"}))
            else:
                await websocket.send(json.dumps({"type": "rejected", "reason": "observe_only_no_action_dispatch"}))
    except (asyncio.TimeoutError, ConnectionClosed):
        return


async def main() -> None:
    token = load_token()
    print("Darkspirit browser bridge — OBSERVE-ONLY")
    print(f"Listening only on ws://{HOST}:{PORT}")
    print(f"Bridge token saved to: {TOKEN_FILE}")
    print("No computer actions are enabled in this scaffold.")
    async with serve(handler, HOST, PORT, max_size=MAX_MESSAGE_BYTES, origins=["chrome-extension://", "http://localhost", "http://127.0.0.1"]):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBridge stopped.")
