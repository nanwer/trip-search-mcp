# Setup Guide

End-to-end walk-through for installing the Flight Search MCP server and
connecting it to your Claude client. Plan ~5 minutes the first time.

If you just want the cheatsheet, the [README](../README.md#install-one-time-3-minutes)
has it. This document is the verbose, "I want to understand what each step
does" version.

---

## Prerequisites

- **macOS, Linux, or Windows.** The server itself is platform-neutral. Claude
  Desktop configuration paths differ — both are covered below.
- **Python 3.12 or newer.** Check with `python3 --version`. If you're below
  3.12, install a newer Python first. The easiest way is [uv](https://docs.astral.sh/uv/)
  (one-line install) which can manage Python versions for you.
- **No API key needed.** The server uses [fli](https://github.com/punitarani/fli),
  which talks to Google Flights' public endpoints directly.

---

## Step 1 — Clone the repository

```bash
git clone https://github.com/nanwer/trip-search-mcp.git
cd trip-search-mcp
```

If you don't have `git`, install it first. On macOS the easiest way is
`xcode-select --install`. Or grab it from <https://git-scm.com>.

---

## Step 2 — Install the Python package

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
.venv/bin/python -c "from trip_search_mcp.server import mcp; print(mcp.name)"
```

Should print `trip-search-mcp`. If you see an `ImportError`, the install didn't
complete — re-run the previous command and read the output.

---

## Step 3 — (optional) Get a SerpAPI key for hotels

The flight tools (`search_flights`, `search_cheapest_dates`) need no API
keys. **The hotel tool (`search_hotels`) needs a free SerpAPI key.** If
you only care about flights, skip this step entirely; the server starts
fine without one and `search_hotels` will surface a clear "set
SERPAPI_KEY" message if anyone calls it.

To enable hotels:

1. Sign up at <https://serpapi.com>. Free tier gives 100 searches/month.
2. Copy your key from <https://serpapi.com/manage-api-key>.
3. You'll paste it into the MCP client config in the next step.

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
echo '{}' > "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
```

Add a `mcpServers` entry. If you already have other MCP servers, just add
`flights` alongside them (don't forget to put a comma after the previous
entry).

**Flights-only (no hotels):**

```json
{
  "mcpServers": {
    "trip-search": {
      "command": "/ABSOLUTE/PATH/TO/trip-search-mcp/.venv/bin/python",
      "args": ["-m", "trip_search_mcp.server"]
    }
  }
}
```

**With hotels enabled:** add the `env` block with your SerpAPI key.

```json
{
  "mcpServers": {
    "trip-search": {
      "command": "/ABSOLUTE/PATH/TO/trip-search-mcp/.venv/bin/python",
      "args": ["-m", "trip_search_mcp.server"],
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
# → /Users/yourname/code/trip-search-mcp/.venv/bin/python
```

Save the file, then **fully quit Claude Desktop with ⌘Q** and reopen. Closing
the window isn't enough.

### Claude Desktop (Windows)

Same shape, different path. Open
`%APPDATA%\Claude\claude_desktop_config.json` in a text editor and add the
same `mcpServers.flights` block. Replace the path with your actual install
location, using Windows backslashes. Use the same `env` block to enable
hotels.

```json
{
  "mcpServers": {
    "trip-search": {
      "command": "C:\\path\\to\\trip-search-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "trip_search_mcp.server"],
      "env": {
        "SERPAPI_KEY": "paste-your-serpapi-key-here"
      }
    }
  }
}
```

### Claude Code (CLI)

Flights-only:

```bash
claude mcp add trip-search \
  -- /ABSOLUTE/PATH/TO/trip-search-mcp/.venv/bin/python -m trip_search_mcp.server
```

With hotels enabled:

```bash
claude mcp add trip-search \
  --env SERPAPI_KEY=paste-your-serpapi-key-here \
  -- /ABSOLUTE/PATH/TO/trip-search-mcp/.venv/bin/python -m trip_search_mcp.server
```

Then `claude` to start a session; the tools are available.

### Other clients

Any MCP client that supports the stdio transport. Use:
- **Command:** `/ABSOLUTE/PATH/TO/trip-search-mcp/.venv/bin/python`
- **Arguments:** `-m trip_search_mcp.server`
- **Environment (optional):** `SERPAPI_KEY=<your-key>` to enable `search_hotels`.

---

## Step 5 — Use it

Start a new chat. Click the hammer/tools icon at the bottom of the message
input. You should see `flights` with up to **three tools**:

- `search_flights` — specific-date flight search (always available)
- `search_cheapest_dates` — date-flex price grid (always available)
- `search_hotels` — Google Hotels search (only useful if you set SERPAPI_KEY)

Ask Claude in plain English:

> **Flights:**
> "Find me round-trip flights from Helsinki to Washington DC for May 18
> returning May 29, 1 adult, in economy. Summarize the cheapest options."
>
> **Date-flex:**
> "What's the cheapest week to go from London to Tokyo in March for a
> 10-day trip?"
>
> **Hotels (requires SERPAPI_KEY):**
> "Find me hotels in Tampere from June 15 to June 18, 2 adults, at least
> 4 stars, with pool and wifi, cheapest first."

Claude picks the right tool, waits a few seconds for the response, and
writes you a summary with a clickable "Book on Google Flights" /
"Book on Google Hotels" link per offer.

---

## Troubleshooting

### The `flights` server doesn't appear in Claude Desktop's tools menu

1. Did you fully quit Claude Desktop with ⌘Q? Closing the window keeps the
   process running with the old config.
2. Is the `command` field an absolute path? Relative paths fail silently.
3. Open the Claude Desktop log: `~/Library/Logs/Claude/mcp*.log`. Look for a
   line like `flights: stdio process exited with code X` and a traceback.

### `search_hotels` returns `auth_failed` ("SERPAPI_KEY is not set")

The server started without a SerpAPI key. Add the `env` block to your
Claude Desktop config (see Step 4) with `SERPAPI_KEY` set, then ⌘Q +
reopen Claude Desktop. The flight tools keep working regardless.

### Searches time out for 4+ minutes (or hang silently) after pulling new code

Claude Desktop launches the MCP server as a subprocess **once** when it
starts up. Pulling new code and editing `claude_desktop_config.json` does
not reload the running subprocess — it keeps executing whatever code was on
disk when Claude Desktop first launched it.

Diagnose:
```bash
ps -o lstart=,command= -p $(pgrep -f trip_search_mcp.server | head -1)
```

If the start time is older than your last `git pull`, the subprocess is
stale. Fix:

1. **⌘Q Claude Desktop** (full quit; closing the window is not enough).
2. Reopen Claude Desktop.
3. Re-run the `ps` command above; the start time should now be recent.

The same applies after any `pip install -e .` rebuild or git pull that
changes server code.

### "ModuleNotFoundError: No module named 'trip_search_mcp'"

The Python in `command` doesn't have the package installed. Either:
- You pointed to the system Python, not the project venv. Fix the path.
- The install didn't complete. Re-run `pip install -e .` and confirm with
  the verify command in Step 2.

### Claude calls the tool and gets `{"error": {"code": "rate_limited", ...}}`

Google occasionally throttles the underlying API. fli retries automatically;
if you still see this, the throttle is sustained. Wait a few minutes and
retry.

### Claude calls the tool and gets `{"error": {"code": "upstream_error", ...}}`

Google's API may have changed. Check
<https://github.com/punitarani/fli/issues> for an open issue. If it's broken
upstream, roll back to the SerpAPI-based version: `git checkout pre-fli-migration`.

### A search takes 15+ seconds

Round-trip searches against Google can be slow under load. fli has built-in
retry/backoff for transient 429s, which adds latency when Google is busy.
If the search times out, retry once.

### A search returns `{"error": {"code": "invalid_input", ...}}` with "not recognized by Google Flights"

You're using an airport or airline IATA code that fli doesn't know. Try a
larger/better-known code (e.g., use `JFK` not the local regional airport's
code).

---

## Optional: local verification before connecting Claude

To test the integration without going through Claude Desktop:

```bash
.venv/bin/python -c "
import asyncio, json
from trip_search_mcp.server import search_flights_tool
r = asyncio.run(search_flights_tool.fn(
    origin='HEL', destination='IAD',
    departure_date='2026-05-18', return_date='2026-05-29', adults=1,
))
print(json.dumps(r, indent=2))
"
```

If you get a `results` array, the server is healthy and the issue (if any)
is in the Claude Desktop config rather than the server. Takes about 10
seconds.

---

## What next?

- Check out [the README](../README.md) for the tool reference and
  architecture overview.
- The fli migration plan lives in [MIGRATION-FLI-SPEC.md](../MIGRATION-FLI-SPEC.md).
- The original Phase 1 spec is at [SPEC.md](../SPEC.md).
- Issues, feature requests, or "this didn't work for me on $platform": open
  an issue on the GitHub repo.
