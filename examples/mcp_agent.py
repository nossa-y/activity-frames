"""End-to-end example of an agent connecting to activity-frames via MCP over stdio.

This script demonstrates how an AI agent or application can:
1. Spawn the `activity-frames` MCP server as a stdio subprocess.
2. Complete the standard MCP protocol handshake (initialize, notifications/initialized, tools/list).
3. Call `get_context` to fetch compact, measured screen activity context.
4. Call `get_activity` to inspect structured JSON frames.
5. Pass the retrieved context to an LLM loop to answer user questions grounded in actual screen activity.

Zero external dependencies required to run the client loop:
    python examples/mcp_agent.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional


class MCPClient:
    """Minimal stdio JSON-RPC client for Model Context Protocol (MCP)."""

    def __init__(self) -> None:
        env = dict(os.environ)
        src_path = str(Path(__file__).resolve().parent.parent / "src")
        env["PYTHONPATH"] = f"{src_path}:{env.get('PYTHONPATH', '')}".rstrip(":")

        # Spawn the activity-frames MCP server over stdio
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "activity_frames.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._msg_id = 0

    def send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a JSON-RPC request and wait for the response line."""
        self._msg_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": self._msg_id,
            "method": method,
        }
        if params is not None:
            req["params"] = params

        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()

        assert self.proc.stdout is not None
        response_line = self.proc.stdout.readline()
        if not response_line:
            raise RuntimeError("MCP server closed stream unexpectedly")

        return json.loads(response_line)

    def send_notification(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        req = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            req["params"] = params

        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(req) + "\n")
        self.proc.stdin.flush()

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
        """Call an MCP tool on the server and return the text result."""
        resp = self.send_request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })

        if "error" in resp:
            raise RuntimeError(f"MCP error calling {name}: {resp['error']}")

        content_items = resp.get("result", {}).get("content", [])
        if content_items and content_items[0].get("type") == "text":
            return content_items[0]["text"]
        return ""

    def close(self) -> None:
        """Close stdio streams and terminate process."""
        if self.proc:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait()


def main() -> None:
    client = MCPClient()
    try:
        print("=== 1. Initializing MCP Connection ===")
        init_resp = client.send_request("initialize", {"protocolVersion": "2024-11-05"})
        server_info = init_resp.get("result", {}).get("serverInfo", {})
        print(f"Connected to MCP Server: {server_info.get('name')} (v{server_info.get('version')})")

        # Notify server initialization completed
        client.send_notification("notifications/initialized")

        print("\n=== 2. Discovering Server Tools ===")
        tools_resp = client.send_request("tools/list")
        tools = tools_resp.get("result", {}).get("tools", [])
        for tool in tools:
            print(f"- {tool['name']}: {tool['description'][:70]}...")

        print("\n=== 3. Calling get_context tool ===")
        context = client.call_tool("get_context", {"hours": 2})
        print("--- Context Block Received ---")
        print(context if context.strip() else "(No recent activity recorded)")

        print("\n=== 4. Calling get_activity tool ===")
        activity_json = client.call_tool("get_activity", {"hours": 2})
        if activity_json.startswith("Error:"):
            print(f"Server message: {activity_json}")
        else:
            try:
                activity_data = json.loads(activity_json)
                frame_count = len(activity_data.get("frames", []))
                print(f"Retrieved {frame_count} structured activity frames for the last 2 hours.")
            except json.JSONDecodeError:
                print(f"Raw response: {activity_json}")

        print("\n=== 5. How your agent uses this ===")
        system_prompt = (
            "You are an AI desktop assistant. Below is the user's actual screen activity "
            "for the last 2 hours, measured deterministically by activity-frames:\n\n"
            f"{context}\n\n"
            "Use this activity data to answer user queries grounded in facts."
        )
        print("Example Prompt constructed for LLM (Anthropic / OpenAI / Ollama):\n")
        print(system_prompt[:300] + "...\n")

        print("Pseudocode for agent completion:")
        print("  # response = anthropic_client.messages.create(")
        print("  #     model='claude-3-5-sonnet-20241022',")
        print("  #     system=system_prompt,")
        print("  #     messages=[{'role': 'user', 'content': 'What was I working on before lunch?'}]")
        print("  # )")

    finally:
        client.close()


if __name__ == "__main__":
    main()
