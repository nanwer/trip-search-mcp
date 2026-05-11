# Setup Guide

End-to-end walk-through for installing the Flight Search MCP server and
connecting it to your Claude client. Plan ~10 minutes the first time.

If you just want the cheatsheet, the [README](../README.md#install-one-time-5-minutes)
has it. This document is the verbose, "I want to understand what each step
does" version.

---

## Prerequisites

- **macOS, Linux, or Windows.** The server itself is platform-neutral. Claude
  Desktop configuration paths differ — both are covered below.
- **Python 3.12 or newer.** Check with `python3 --version`. If you're below
  3.12, install a newer Python first. The easiest way is [uv](https://docs.astral.sh/uv/)
  (one-line install) which can manage Python versions for you.
- **A SerpAPI account.** Free tier is enough for personal use.

---

## Step 1 — Get a SerpAPI key

[SerpAPI](https://serpapi.com) wraps Google search APIs, including Google
Flights. This MCP server uses their Google Flights endpoint as its data
source.

1. Go to <https://serpapi.com> and click **Register**.
2. After verifying your email and logging in, head to
   <https://serpapi.com/manage-api-key>.
3. Copy the **Private API Key** string. You'll paste it into the config file
   later. Keep it secret — anyone with this key can spend your monthly quota.

**Free tier specs:** 100 searches/month, no credit card required.

---

## Step 2 — Clone the repository

```bash
git clone https://github.com/nanwer/flights-mcp.git
cd flights-mcp
```

If you don't have `git`, install it first. On macOS the easiest way is
`xcode-select --install`. Or grab it from <https://git-scm.com>.

---

## Step 3 — Install the Python package

You have two options. **Use `uv` if you can** — it's faster and doesn't
require activating a virtual environment by hand.

### Option A — `uv` (recommended)

If you don't have `uv` yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then from the project directory:

```bash
uv venv
uv pip install -e .
```

This creates `.venv/` in the project root and installs the package into it.

### Option B — `pip` with venv

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

### Verify

```bash
.venv/bin/python -c "from flights_mcp.server import mcp; print(mcp.name)"
```

Should print `flights-mcp`. If you see an `ImportError`, the install didn't
complete — re-run the previous command and read the output.

---

## Step 4 — Connect to Claude

### Claude Desktop (macOS)

Open the config file:

```bash
open -e "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
```

If the file doesn't exist yet, create it first:

```bash
mkdir -p "$HOME/Library/Application Support/Claude"
touch "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
echo '{}' > "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
```

Add a `mcpServers` entry. If you already have other MCP servers, just add
`flights` alongside them (don't forget to put a comma after the previous
entry). Replace the two values marked `# CHANGE THIS`:

```json
{
  "mcpServers": {
    "flights": {
      "command": "/ABSOLUTE/PATH/TO/flights-mcp/.venv/bin/python",
      "args": ["-m", "flights_mcp.server"],
      "env": {
        "SERPAPI_KEY": "paste-your-serpapi-key-here"
      }
    }
  }
}
```

The `command` field MUST be an absolute path — Claude Desktop doesn't know
your shell's `cd`. To find the absolute path, run `pwd` inside the cloned
repo and append `/.venv/bin/python`. Example:

```bash
echo "$(pwd)/.venv/bin/python"
# → /Users/yourname/code/flights-mcp/.venv/bin/python
```

Save the file, then **fully quit Claude Desktop with ⌘Q** and reopen. Closing
the window isn't enough.

### Claude Desktop (Windows)

Same shape, different path. Open
`%APPDATA%\Claude\claude_desktop_config.json` in a text editor and add the
same `mcpServers.flights` block. Replace the path with your actual install
location, using Windows backslashes:

```json
{
  "mcpServers": {
    "flights": {
      "command": "C:\\path\\to\\flights-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "flights_mcp.server"],
      "env": {
        "SERPAPI_KEY": "paste-your-serpapi-key-here"
      }
    }
  }
}
```

### Claude Code (CLI)

```bash
claude mcp add flights \
  --env SERPAPI_KEY=paste-your-serpapi-key-here \
  -- /ABSOLUTE/PATH/TO/flights-mcp/.venv/bin/python -m flights_mcp.server
```

Then `claude` to start a session; the tool is available.

### Other clients

Any MCP client that supports the stdio transport. Use:
- **Command:** `/ABSOLUTE/PATH/TO/flights-mcp/.venv/bin/python`
- **Arguments:** `-m flights_mcp.server`
- **Environment:** `SERPAPI_KEY=<your-key>`

---

## Step 5 — Use it

Start a new chat. Click the hammer/tools icon at the bottom of the message
input. You should see `flights` listed with one tool: `search_flights`.

Ask Claude in plain English:

> Find me round-trip flights from Helsinki to Washington DC for May 18
> returning May 29, 1 adult, in economy. Summarize the cheapest options.

Claude calls the tool (you'll see a "Running search_flights..." indicator),
waits ~5 seconds for SerpAPI to respond, and writes you a summary.

---

## Troubleshooting

### The `flights` tool doesn't appear in Claude Desktop's tools menu

1. Did you fully quit Claude Desktop with ⌘Q? Closing the window keeps the
   process running with the old config.
2. Is the `command` field an absolute path? Relative paths fail silently.
3. Open the Claude Desktop log: ~/Library/Logs/Claude/mcp*.log. Look for a
   line like `flights: stdio process exited with code X` and a traceback.

### "ModuleNotFoundError: No module named 'flights_mcp'"

The Python in `command` doesn't have the package installed. Either:
- You pointed to the system Python, not the project venv. Fix the path.
- The install didn't complete. Re-run `pip install -e .` and confirm with
  the verify command in Step 3.

### "RuntimeError: Required environment variable 'SERPAPI_KEY' is not set"

The `env` block in the config is missing or the variable name has a typo.
Open the config and check spelling. The variable is `SERPAPI_KEY` exactly.

### Claude calls the tool and gets `{"error": {"code": "auth_failed", ...}}`

Your SerpAPI key is wrong. Get the current key from
<https://serpapi.com/manage-api-key> and update the config.

### Claude calls the tool and gets `{"error": {"code": "quota_exceeded", ...}}`

You've used your 100 free searches for the month. SerpAPI's quota resets
monthly. Upgrade your plan or wait.

### A search takes 30+ seconds

Round-trip searches are 1 + N upstream API calls (1 for outbound, N for
return legs). With `max_results=5` (cap) that's 6 calls × ~3s each. The
implementation parallelizes the N return-leg calls, so the realistic worst
case is more like 6-8 seconds end-to-end. If you're seeing more, SerpAPI is
slow today — try again later or use a smaller `max_results`.

### A search returns `{"error": {"code": "invalid_input", "message": "max_results 10 exceeds the round-trip cap of 5..."}}`

Round-trip queries are capped at 5 results because each result costs a
separate upstream call. Tell Claude to ask for fewer, or omit `return_date`
for a one-way search (capped at 50).

---

## Optional: local verification before connecting Claude

To test the integration without going through Claude Desktop:

```bash
set -a; source .env; set +a
.venv/bin/python -c "
import asyncio, json
from flights_mcp.server import search_flights_tool
r = asyncio.run(search_flights_tool.fn(
    origin='HEL', destination='IAD',
    departure_date='2026-05-18', return_date='2026-05-29', adults=1,
))
print(json.dumps(r, indent=2))
"
```

If you get a `results` array, the server is healthy and the issue (if any)
is in the Claude Desktop config rather than the server. Cost: ~4 SerpAPI
calls.

---

## What next?

- Check out [the README](../README.md) for the tool reference and
  architecture overview.
- The full Phase 1 functional spec lives in [SPEC.md](../SPEC.md).
- The implementation plan is at
  [docs/superpowers/plans/2026-05-11-flight-search-mcp-phase1.md](./superpowers/plans/2026-05-11-flight-search-mcp-phase1.md).
- Issues, feature requests, or "this didn't work for me on $platform": open
  an issue on the GitHub repo.
