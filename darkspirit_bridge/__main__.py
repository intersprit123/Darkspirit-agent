"""Localhost bridge from browser AI to selected Windows-Use tools.

Only a small allow-list is exposed. Shell, file, memory, and arbitrary-code tools are
intentionally unavailable to browser-originated requests.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

HOST = "127.0.0.1"
PORT = 17613
VOICE_HOST = "127.0.0.1"
VOICE_PORT = 17614
VOICE_APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "DarkspiritAgent"
VOICE_TOKEN_PATH = VOICE_APP_DIR / "voice-bridge.token"
_voice_token = secrets.token_urlsafe(32)
_voice_queue: deque[dict[str, Any]] = deque(maxlen=20)
_voice_queue_lock = threading.Lock()
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_TOOL_OUTPUT_CHARS = 10_000
MAX_SCREENSHOT_BYTES = 1_800_000
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_LONG_NUMBER_RE = re.compile(r"\b(?:\d[ -]?){8,}\b")
EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")

ACTION_TOOLS = {
    "app_tool",
    "click_tool",
    "type_tool",
    "shortcut_tool",
    "scroll_tool",
    "move_tool",
    "wait_tool",
    "shell_tool",
    "desktop_tool",
    "multi_select_tool",
    "multi_edit_tool",
    "file_tool",
    "memory_tool",
    "move_mouse_continuously",
}
READ_ONLY_TOOLS = {"scrape_tool"}
_actions_enabled = False
_screenshots_enabled = False
_desktop = None
_tool_map: dict[str, Any] | None = None


def get_desktop():
    """Initialize Windows-Use UI automation only when the user asks for desktop state."""
    global _desktop
    if _desktop is None:
        from windows_use.agent.desktop.service import Desktop

        _desktop = Desktop(use_vision=False, use_accessibility=True)
    return _desktop


def get_tool_map() -> dict[str, Any]:
    """Return only tools permitted through the browser bridge."""
    global _tool_map
    if _tool_map is None:
        from windows_use.agent.tools import BUILTIN_TOOLS, EXPERIMENTAL_TOOLS

        all_tools = [*BUILTIN_TOOLS, *EXPERIMENTAL_TOOLS]
        _tool_map = {
            tool.name: tool
            for tool in all_tools
            if tool.name in ACTION_TOOLS or tool.name in READ_ONLY_TOOLS
        }
    return _tool_map


def get_tool_catalog() -> list[dict[str, Any]]:
    """Build a compact catalogue without exposing private reasoning fields."""
    tools = []
    for name, tool in get_tool_map().items():
        schema = tool.json_schema
        parameters = schema.get("parameters", {"type": "object", "properties": {}})
        properties = dict(parameters.get("properties", {}))
        properties.pop("thought", None)
        required = [item for item in parameters.get("required", []) if item != "thought"]
        tools.append(
            {
                "name": name,
                "description": schema.get("description", ""),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
        )
    tools.extend(
        [
            {
                "name": "get_cursor_position",
                "description": "Read-only: return the current mouse cursor coordinates.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "get_screen_info",
                "description": "Read-only: return screen dimensions, cursor position, active window bounds, and visible window titles.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "move_mouse_continuously",
                "description": "Move the cursor repeatedly through a specified path for a short, bounded duration. Requires computer actions enabled and user approval. Maximum 20 seconds; the Stop button cancels it at the next movement interval.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "points": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "minItems": 2,
                                "maxItems": 2,
                            },
                            "minItems": 2,
                            "maxItems": 20,
                            "description": "A path of 2-20 [x, y] screen coordinates to cycle through.",
                        },
                        "duration_seconds": {
                            "type": "number",
                            "minimum": 1,
                            "maximum": 20,
                            "description": "How long to move, from 1 to 20 seconds.",
                        },
                        "interval_ms": {
                            "type": "integer",
                            "minimum": 50,
                            "maximum": 500,
                            "description": "Pause between cursor moves, from 50 to 500 milliseconds.",
                        },
                    },
                    "required": ["points", "duration_seconds"],
                },
            },
            {
                "name": "get_desktop_state",
                "description": (
                    "Read-only: list current window titles and visible interactive desktop "
                    "elements. This information will be sent to the AI website in this chat."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            *(
                [
                    {
                        "name": "capture_screenshot",
                        "description": "Capture the current desktop as an image and attach it to this AI conversation. Only available when the user explicitly enables screenshot sharing in the panel.",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    }
                ]
                if _screenshots_enabled
                else []
            ),
            {
                "name": "done_tool",
                "description": "Finish the task and show a final answer to the user.",
                "parameters": {
                    "type": "object",
                    "properties": {"answer": {"type": "string", "description": "Final response"}},
                    "required": ["answer"],
                },
            },
        ]
    )
    return tools


def _safe_label(value: Any, control_type: str = "") -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if any(word in control_type.lower() for word in ("edit", "textbox", "document", "password")):
        return "[input contents hidden]"
    text = _EMAIL_RE.sub("[redacted email]", text)
    text = _LONG_NUMBER_RE.sub("[redacted number]", text)
    return text[:160]


def desktop_snapshot() -> str:
    """Return a bounded accessibility summary with input values and metadata omitted."""
    state = get_desktop().get_state()
    active = _safe_label(state.active_window.name) if state.active_window else "None"
    windows = [_safe_label(window.name) for window in state.windows[:30]]
    tree = state.tree_state
    interactive = ["# id|window|control_type|name|coords"]
    scrollable = ["# id|window|control_type|name|coords"]
    if tree and tree.status:
        for index, node in enumerate((tree.interactive_nodes or [])[:70]):
            interactive.append(
                str(index)
                + "|"
                + _safe_label(node.window_name)
                + "|"
                + _safe_label(node.control_type)
                + "|"
                + _safe_label(node.name, node.control_type)
                + "|"
                + node.center.to_string()
            )
        base = len(tree.interactive_nodes or [])
        for offset, node in enumerate((tree.scrollable_nodes or [])[:25]):
            scrollable.append(
                str(base + offset)
                + "|"
                + _safe_label(node.window_name)
                + "|"
                + _safe_label(node.control_type)
                + "|"
                + _safe_label(node.name, node.control_type)
                + "|"
                + node.center.to_string()
            )
    else:
        interactive.append("Accessibility tree unavailable.")
    return (
        "Active window: "
        + active
        + "\n"
        + "Other visible windows: "
        + json.dumps(windows, ensure_ascii=False)
        + "\n\n"
        + "Interactive elements (input values omitted):\n"
        + "\n".join(interactive)[:8000]
        + "\n\n"
        + "Scrollable elements: \n"
        + "\n".join(scrollable)[:2500]
    )[:MAX_TOOL_OUTPUT_CHARS]


async def send_response(websocket, kind: str, rid: Any, **values: Any) -> None:
    payload = {"type": kind, "id": rid, **values}
    await websocket.send(json.dumps(payload, ensure_ascii=False))


async def handle_message(websocket, message: dict[str, Any]) -> None:
    global _actions_enabled, _screenshots_enabled
    rid = message.get("id")
    kind = message.get("type")

    if kind == "ping":
        await send_response(websocket, "pong", rid, mode="browser-ai")
        return
    if kind == "list_tools":
        await send_response(
            websocket,
            "tools",
            rid,
            tools=get_tool_catalog(),
            actions_enabled=_actions_enabled,
            policy={
                "shell": "available only when computer actions are enabled and the action is approved",
                "file_operations": "available only when computer actions are enabled and the action is approved",
                "arbitrary_code": False,
                "screenshots": "only when screenshot sharing is explicitly enabled",
                "action_approval": "required in browser extension by default",
            },
        )
        return
    if kind == "set_actions":
        _actions_enabled = bool(message.get("enabled", False))
        await send_response(websocket, "actions_mode", rid, actions_enabled=_actions_enabled)
        print(
            "[bridge] Computer actions "
            + ("ENABLED for this session." if _actions_enabled else "disabled.")
        )
        return
    if kind == "set_screenshots":
        _screenshots_enabled = bool(message.get("enabled", False))
        await send_response(
            websocket, "screenshots_mode", rid, screenshots_enabled=_screenshots_enabled
        )
        print(
            "[bridge] Automatic screenshot sharing "
            + ("ENABLED for this session." if _screenshots_enabled else "disabled.")
        )
        return
    if kind != "call_tool":
        await send_response(websocket, "error", rid, error="Unsupported request type.")
        return

    name = message.get("name")
    arguments = message.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        await send_response(websocket, "tool_result", rid, ok=False, error="Invalid tool request.")
        return

    if name == "get_desktop_state":
        try:
            output = desktop_snapshot()
            await send_response(websocket, "tool_result", rid, ok=True, text=output, read_only=True)
        except Exception as exc:
            await send_response(
                websocket,
                "tool_result",
                rid,
                ok=False,
                error="Could not read desktop state: " + type(exc).__name__ + ": " + str(exc),
            )
        return

    if name in {"get_cursor_position", "get_screen_info"}:
        try:
            desktop = get_desktop()
            cursor = desktop.get_cursor_location()
            if name == "get_cursor_position":
                output = json.dumps({"x": int(cursor[0]), "y": int(cursor[1])})
            else:
                state = desktop.get_state()
                size = desktop.get_screen_size()
                active = state.active_window
                windows = [
                    {
                        "title": _safe_label(window.name),
                        "x": window.bounding_box.left,
                        "y": window.bounding_box.top,
                        "width": window.bounding_box.width,
                        "height": window.bounding_box.height,
                        "status": str(window.status),
                    }
                    for window in ([active] if active else []) + list(state.windows[:25])
                ]
                output = json.dumps(
                    {
                        "screen": {"width": int(size.width), "height": int(size.height)},
                        "cursor": {"x": int(cursor[0]), "y": int(cursor[1])},
                        "active_window": _safe_label(active.name) if active else None,
                        "windows": windows,
                    },
                    ensure_ascii=False,
                )
            await send_response(websocket, "tool_result", rid, ok=True, text=output, read_only=True)
        except Exception as exc:
            await send_response(
                websocket,
                "tool_result",
                rid,
                ok=False,
                error="Could not inspect screen: " + type(exc).__name__ + ": " + str(exc),
            )
        return

    if name == "capture_screenshot":
        if not _screenshots_enabled:
            await send_response(
                websocket,
                "tool_result",
                rid,
                ok=False,
                error="Screenshot sharing is off. Ask the user to enable the screenshot-sharing checkbox first.",
            )
            return
        try:
            screenshot = get_desktop().get_screenshot()
            screenshot.thumbnail((1600, 1000))
            buffer = io.BytesIO()
            screenshot.convert("RGB").save(buffer, format="JPEG", quality=58, optimize=True)
            image_bytes = buffer.getvalue()
            if len(image_bytes) > MAX_SCREENSHOT_BYTES:
                buffer = io.BytesIO()
                screenshot.thumbnail((1280, 800))
                screenshot.convert("RGB").save(buffer, format="JPEG", quality=42, optimize=True)
                image_bytes = buffer.getvalue()
            if not image_bytes or len(image_bytes) > MAX_SCREENSHOT_BYTES:
                raise ValueError("Screenshot exceeds the safe transfer-size limit.")
            image_payload = {
                "mime_type": "image/jpeg",
                "filename": "darkspirit-desktop.jpg",
                "width": int(screenshot.width),
                "height": int(screenshot.height),
                "data": base64.b64encode(image_bytes).decode("ascii"),
            }
            await send_response(
                websocket,
                "tool_result",
                rid,
                ok=True,
                text="Desktop screenshot captured for attachment to the AI conversation.",
                screenshot=image_payload,
                read_only=True,
            )
        except Exception as exc:
            await send_response(
                websocket,
                "tool_result",
                rid,
                ok=False,
                error="Could not capture screenshot: " + type(exc).__name__ + ": " + str(exc),
            )
        return

    if name == "move_mouse_continuously":
        if not _actions_enabled:
            await send_response(
                websocket,
                "tool_result",
                rid,
                ok=False,
                error="Computer actions are disabled. Enable them in the extension and start a new task.",
            )
            return
        points = arguments.get("points")
        duration = arguments.get("duration_seconds", 5)
        interval_ms = arguments.get("interval_ms", 120)
        if (
            not isinstance(points, list)
            or not 2 <= len(points) <= 20
            or not isinstance(duration, int | float)
            or isinstance(duration, bool)
            or not 1 <= float(duration) <= 20
            or not isinstance(interval_ms, int)
            or isinstance(interval_ms, bool)
            or not 50 <= interval_ms <= 500
        ):
            await send_response(
                websocket,
                "tool_result",
                rid,
                ok=False,
                error="Invalid continuous movement settings. Supply 2-20 points, duration_seconds 1-20, and interval_ms 50-500.",
            )
            return
        normalized_points = []
        try:
            for point in points:
                if (
                    not isinstance(point, list)
                    or len(point) != 2
                    or any(not isinstance(value, int) or isinstance(value, bool) for value in point)
                ):
                    raise ValueError("Each point must be an integer [x, y] pair.")
                normalized_points.append((point[0], point[1]))
            screen = get_desktop().get_screen_size()
            if any(
                x < 0 or y < 0 or x >= screen.width or y >= screen.height
                for x, y in normalized_points
            ):
                raise ValueError("All mouse points must be inside the primary screen bounds.")
        except Exception as exc:
            await send_response(
                websocket, "tool_result", rid, ok=False, error="Invalid mouse path: " + str(exc)
            )
            return
        started = time.monotonic()
        moves = 0
        index = 0
        try:
            while time.monotonic() - started < float(duration):
                if not _actions_enabled:
                    break
                x, y = normalized_points[index % len(normalized_points)]
                get_desktop().move((x, y))
                moves += 1
                index += 1
                await asyncio.sleep(interval_ms / 1000)
            stopped_early = not _actions_enabled
            await send_response(
                websocket,
                "tool_result",
                rid,
                ok=True,
                text=json.dumps(
                    {
                        "moves_completed": moves,
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                        "stopped_early": stopped_early,
                        "note": "Mouse movement only; no click or drag was performed.",
                    }
                ),
            )
        except Exception as exc:
            await send_response(
                websocket,
                "tool_result",
                rid,
                ok=False,
                error="Continuous mouse movement failed: " + type(exc).__name__ + ": " + str(exc),
            )
        return

    if name not in ACTION_TOOLS and name not in READ_ONLY_TOOLS:
        await send_response(
            websocket,
            "tool_result",
            rid,
            ok=False,
            error="Tool not allowed by Darkspirit Agent bridge policy.",
        )
        return
    if name in ACTION_TOOLS and not _actions_enabled:
        await send_response(
            websocket,
            "tool_result",
            rid,
            ok=False,
            error="Computer actions are disabled. Enable them in the extension and start a new task.",
        )
        return

    tool = get_tool_map()[name]
    arguments = dict(arguments)
    # These models require the field; do not ask the website for private reasoning.
    arguments["thought"] = "Approved through Darkspirit Agent browser bridge."
    errors = tool.validate_params(arguments)
    if errors:
        await send_response(
            websocket,
            "tool_result",
            rid,
            ok=False,
            error="Tool parameters failed validation: " + "; ".join(errors),
        )
        return

    try:
        result = await asyncio.wait_for(
            tool.ainvoke(desktop=get_desktop(), **arguments),
            timeout=45,
        )
        text = str(result)
        await send_response(
            websocket, "tool_result", rid, ok=True, text=text[:MAX_TOOL_OUTPUT_CHARS]
        )
        print("[bridge] Approved tool completed: " + name)
    except asyncio.TimeoutError:
        await send_response(websocket, "tool_result", rid, ok=False, error="Tool timed out.")
    except Exception as exc:
        await send_response(
            websocket,
            "tool_result",
            rid,
            ok=False,
            error="Tool failed: " + type(exc).__name__ + ": " + str(exc),
        )


async def handler(websocket) -> None:
    try:
        first = await asyncio.wait_for(websocket.recv(), timeout=10)
        if not isinstance(first, str) or len(first.encode("utf-8")) > MAX_MESSAGE_BYTES:
            await websocket.close(code=1008, reason="Invalid authentication message")
            return
        try:
            auth = json.loads(first)
        except json.JSONDecodeError:
            await websocket.close(code=1008, reason="Expected JSON authentication")
            return
        if not isinstance(auth, dict) or auth.get("type") != "authenticate":
            await websocket.close(code=1008, reason="Expected bridge handshake")
            return

        await websocket.send(
            json.dumps({"type": "ready", "mode": "browser-ai", "actions_enabled": _actions_enabled})
        )
        async for raw in websocket:
            if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
                await websocket.close(code=1009, reason="Message too large")
                return
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(
                    json.dumps({"type": "error", "id": None, "error": "Invalid JSON"})
                )
                continue
            if not isinstance(message, dict):
                await websocket.send(
                    json.dumps({"type": "error", "id": None, "error": "Expected JSON object"})
                )
                continue
            await handle_message(websocket, message)
    except (asyncio.TimeoutError, ConnectionClosed):
        return


class VoiceHTTPHandler(BaseHTTPRequestHandler):
    """Loopback-only authenticated queue for local companion transcripts."""

    def log_message(self, fmt, *args):
        return

    def _send(self, status: int, body: dict[str, Any], origin: str = "") -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if origin and EXTENSION_ORIGIN.fullmatch(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _authorized_extension(self) -> bool:
        # MV3 extension service-worker fetches can arrive with Origin: null (or
        # without Origin), even when the extension has host_permissions for this
        # loopback URL. The random per-bridge token is the authorization check.
        origin = self.headers.get("Origin", "")
        token = self.headers.get("X-Darkspirit-Token", "")
        origin_is_extension_context = (
            not origin or origin == "null" or bool(EXTENSION_ORIGIN.fullmatch(origin))
        )
        return origin_is_extension_context and secrets.compare_digest(token, _voice_token)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin", "")
        requested_method = self.headers.get("Access-Control-Request-Method", "").upper()
        requested_headers = {
            part.strip().lower()
            for part in self.headers.get("Access-Control-Request-Headers", "").split(",")
            if part.strip()
        }
        if EXTENSION_ORIGIN.fullmatch(origin):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Darkspirit-Token")
            self.send_header("Access-Control-Max-Age", "60")
            self.end_headers()
            return
        # A service-worker request can use an opaque Origin. Permit only the
        # authenticated GET /next preflight in that case; never grant an opaque
        # origin access to /token's response.
        if (
            origin == "null"
            and self.path == "/next"
            and requested_method == "GET"
            and requested_headers == {"x-darkspirit-token"}
        ):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "null")
            self.send_header("Access-Control-Allow-Methods", "GET")
            self.send_header("Access-Control-Allow-Headers", "X-Darkspirit-Token")
            self.send_header("Access-Control-Max-Age", "60")
            self.end_headers()
            return
        self._send(403, {"ok": False, "error": "Origin not allowed"})

    def do_GET(self):
        origin = self.headers.get("Origin", "")
        extension_context = (
            not origin or origin == "null" or bool(EXTENSION_ORIGIN.fullmatch(origin))
        )
        if self.path == "/token" and extension_context:
            body = json.dumps({"ok": True, "token": _voice_token}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            # Only expose the token to an explicitly identified extension origin.
            # Extension service workers with loopback host permission can read
            # the response without CORS headers; ordinary web origins cannot.
            if EXTENSION_ORIGIN.fullmatch(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/next" and self._authorized_extension():
            with _voice_queue_lock:
                item = _voice_queue.popleft() if _voice_queue else None
            if item is None:
                self._send(200, {"ok": True, "text": "", "queued_at": 0}, origin)
            else:
                self._send(
                    200,
                    {
                        "ok": True,
                        "text": item.get("text", ""),
                        "queued_at": item.get("queued_at", 0),
                    },
                    origin,
                )
            return
        self._send(403, {"ok": False, "error": "Unauthorized"}, origin)

    def do_POST(self):
        if self.path != "/submit" or self.headers.get("Origin"):
            self._send(403, {"ok": False, "error": "Local companion only"})
            return
        if not secrets.compare_digest(self.headers.get("X-Darkspirit-Token", ""), _voice_token):
            self._send(403, {"ok": False, "error": "Unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 8192:
                raise ValueError("Invalid payload size")
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            transcript = data.get("text", "") if isinstance(data, dict) else ""
            if not isinstance(transcript, str) or not transcript.strip() or len(transcript) > 4000:
                raise ValueError("Invalid transcript")
            with _voice_queue_lock:
                if len(_voice_queue) >= 20:
                    _voice_queue.popleft()
                _voice_queue.append({"text": transcript.strip(), "queued_at": time.time()})
            self._send(200, {"ok": True})
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            self._send(400, {"ok": False, "error": "Invalid transcript payload"})


def _run_voice_http_server() -> None:
    VOICE_APP_DIR.mkdir(parents=True, exist_ok=True)
    VOICE_TOKEN_PATH.write_text(_voice_token, encoding="utf-8")
    try:
        os.chmod(VOICE_TOKEN_PATH, 0o600)
    except OSError:
        pass
    server = ThreadingHTTPServer((VOICE_HOST, VOICE_PORT), VoiceHTTPHandler)
    server.daemon_threads = True
    server.serve_forever()


async def run_server() -> None:
    print()
    print("Darkspirit Agent — Browser AI Bridge")
    print("Listening locally at ws://" + HOST + ":" + str(PORT))
    print("Mode: actions disabled until explicitly enabled in the extension.")
    threading.Thread(
        target=_run_voice_http_server, name="Darkspirit-Voice-HTTP", daemon=True
    ).start()
    print("Voice queue listening locally at http://" + VOICE_HOST + ":" + str(VOICE_PORT))
    print("Automatic local pairing enabled; no token is required.")
    print("Press Ctrl+C to stop. Port conflicts are reported; no process is killed.")
    async with serve(
        handler,
        HOST,
        PORT,
        max_size=MAX_MESSAGE_BYTES,
        ping_interval=20,
        ping_timeout=20,
        origins=[EXTENSION_ORIGIN],
    ):
        await asyncio.Future()


def cli() -> None:
    """Console entry point."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        print("\nDarkspirit bridge stopped. Computer actions disabled.")


if __name__ == "__main__":
    cli()
