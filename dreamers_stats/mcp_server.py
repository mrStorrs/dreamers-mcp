from __future__ import annotations

import json
import sys
from typing import Any

from . import runtime


PROTOCOL_VERSION = "2025-11-25"


def tool_definitions() -> list[dict[str, Any]]:
    report_schema = {
        "type": "object",
        "properties": {
            "client": {"type": "string", "enum": ["copilot", "codex"]},
            "home": {"type": "string"},
            "repo": {"type": "string", "enum": ["current", "all"]},
            "skill": {"type": "string"},
            "since": {"type": "string"},
            "until": {"type": "string"},
            "output": {"type": "string", "enum": ["json"]},
        },
        "required": ["client", "home"],
    }
    return [
        {
            "name": "doctor",
            "description": "Return stats path health and malformed-line counts for a client home.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "client": {"type": "string", "enum": ["copilot", "codex"]},
                    "home": {"type": "string"},
                },
                "required": ["client", "home"],
            },
        },
        *[
            {
                "name": name,
                "description": f"Return the bounded {name} report for a client stats log.",
                "inputSchema": report_schema,
            }
            for name in ("summarize", "runs", "reviews", "validation", "gates", "tokens")
        ],
        {
            "name": "record_event",
            "description": "Validate, redact, and append a prebuilt stats event to the selected client home.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "client": {"type": "string", "enum": ["copilot", "codex"]},
                    "home": {"type": "string"},
                    "event": {"type": "object"},
                },
                "required": ["client", "home", "event"],
            },
        },
        {
            "name": "record_checkpoint",
            "description": "Build and record a Dreamers checkpoint event for the selected client home.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "client": {"type": "string", "enum": ["copilot", "codex"]},
                    "home": {"type": "string"},
                    "event_type": {"type": "string"},
                    "skill": {"type": "string"},
                    "run_id": {"type": "string"},
                    "status": {"type": "string"},
                    "session_id": {"type": "string"},
                    "branch": {"type": "string"},
                    "repo_path": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "metrics": {"type": "object"},
                },
                "required": ["client", "home", "event_type", "skill", "run_id"],
            },
        },
        {
            "name": "record_hook",
            "description": "Build and record a hook-derived runtime event for the selected client home.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "client": {"type": "string", "enum": ["copilot", "codex"]},
                    "home": {"type": "string"},
                    "event_name": {"type": "string"},
                    "payload": {"type": "object"},
                },
                "required": ["client", "home", "event_name", "payload"],
            },
        },
    ]


def jsonrpc_result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "result": result,
    }


def jsonrpc_error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def tool_success(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "structuredContent": payload,
        "isError": False,
    }


def tool_error(message: str) -> dict[str, Any]:
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def handle_tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    client = arguments.get("client")
    home = arguments.get("home")
    if name == "doctor":
        return tool_success(runtime.doctor(client=client, home=home))
    if name in runtime.REPORT_BUILDERS:
        report = runtime.run_report(
            name,
            client=client,
            home=home,
            repo=arguments.get("repo", "current"),
            skill=arguments.get("skill"),
            since=arguments.get("since"),
            until=arguments.get("until"),
            cwd=arguments.get("cwd"),
        )
        return tool_success(report)
    if name == "record_event":
        event_id = runtime.record_event(arguments["event"], client=client, home=home)
        return tool_success({"event_id": event_id})
    if name == "record_checkpoint":
        namespace = type(
            "CheckpointArgs",
            (),
            {
                "event_type": arguments["event_type"],
                "skill": arguments["skill"],
                "run_id": arguments["run_id"],
                "status": arguments.get("status"),
                "session_id": arguments.get("session_id"),
                "branch": arguments.get("branch"),
                "repo_path": arguments.get("repo_path"),
                "timestamp": arguments.get("timestamp"),
                "metrics_json": json.dumps(arguments.get("metrics", {})),
            },
        )()
        event = runtime.build_checkpoint_event(namespace)
        event_id = runtime.record_event(event, client=client, home=home)
        return tool_success({"event_id": event_id})
    if name == "record_hook":
        event = runtime.build_hook_event(arguments["event_name"], arguments["payload"])
        event_id = runtime.record_event(event, client=client, home=home)
        return tool_success({"event_id": event_id})
    raise runtime.StatsValidationError("invalid_tool", "tool is not supported")


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params", {})

    if method == "initialize":
        return jsonrpc_result(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "dreamers-mcp", "version": "0.1.0"},
                "instructions": "Use report tools for bounded summaries; raw stats writes require explicit tool calls.",
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return jsonrpc_result(message_id, {})
    if method == "tools/list":
        return jsonrpc_result(
            message_id,
            {
                "resultType": "complete",
                "tools": tool_definitions(),
            },
        )
    if method == "tools/call":
        try:
            result = handle_tool_call(params["name"], params.get("arguments", {}))
        except KeyError:
            result = tool_error("missing tool name")
        except (runtime.StatsValidationError, OSError, json.JSONDecodeError) as exc:
            result = tool_error(str(exc))
        return jsonrpc_result(message_id, result)
    if message_id is None:
        return None
    return jsonrpc_error(message_id, -32601, "Method not found")


def serve() -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, sort_keys=True))
            sys.stdout.write("\n")
            sys.stdout.flush()
    return 0


def console_main() -> None:
    raise SystemExit(serve())


if __name__ == "__main__":
    console_main()
