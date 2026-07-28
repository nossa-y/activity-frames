# Examples

## Add it to an MCP client

**Claude Code:**
```bash
claude mcp add activity-frames -- aframes mcp
```

**Cursor / Claude Desktop / any MCP client** (`mcp.json`):
```json
{
  "mcpServers": {
    "activity-frames": {
      "command": "aframes",
      "args": ["mcp"]
    }
  }
}
```

Then ask your agent things like *"what was I working on before lunch?"* or
*"summarize what I did on GitHub today"* - it calls `get_context` / `get_activity`
and answers from your real activity.

## Inject context into any agent (no MCP)

See [`agent_context.py`](agent_context.py). The pattern is one line:

```python
from activity_frames import ActivityLog
context = ActivityLog().context(hours=4)   # compact, paste-ready block
```

Drop `context` into any system prompt. That is the whole integration.

## Files

- [`agent_context.py`](agent_context.py) - build a context block and show what an agent would receive
- [`mcp_agent.py`](mcp_agent.py) - end-to-end example of an agent connecting to `activity-frames` over MCP stdio
- [`daily_standup.py`](daily_standup.py) - turn a day into a first-person standup draft, deterministically
