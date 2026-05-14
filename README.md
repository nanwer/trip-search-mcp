# trip-search-mcp

**Let Claude plan trips for you, in plain English.** Live searches against Google Flights, Google Hotels, vacation rentals, Airbnb, Tripadvisor activities, and event ticket vendors — plus weather forecasts, currency conversion, persistent price watches, and per-property detail drill-downs. Eleven tools, one config block.

```
You:   Find me round-trip flights Helsinki → Washington DC for May 18,
       returning May 29, one stop or fewer.

Claude: [calls search_flights with WAS auto-expanded to IAD, DCA, BWI;
        merges 3 parallel results, ranks cheapest first, returns a
        summary with "Book on Google Flights" links]
```

📋 **[FEATURES.md](./FEATURES.md)** has the full plain-English feature list with paste-ready example prompts for every capability — read that to see what's possible.

📐 **[TRIP-PLANNING-EXPANSION-SPEC.md](./TRIP-PLANNING-EXPANSION-SPEC.md)** tracks the five-track expansion plan (weather, currency, events, activities, drill-down). Weather is shipped; the other four are queued.

---

## Before you start

You need:

- A computer running **macOS, Windows, or Linux**
- **[Claude Desktop](https://claude.ai/download)**, signed in
- About **5 minutes** the first time

You do NOT need an account anywhere except Claude — unless you also want hotel search, which uses a free SerpAPI key (covered as an optional step below).

---

## Install — step by step

Everything below happens in your **Terminal app** (macOS/Linux) or **PowerShell** (Windows).

> **Don't know what a terminal is?** macOS: press `⌘+Space`, type `Terminal`, press Enter. Windows: press the Win key, type `PowerShell`, press Enter.

### 1. Install Python 3.12 (skip if you already have it)

```bash
python3 --version
```

If you see `Python 3.12.x` or higher, jump to step 2. Otherwise install `uv` — one line, brings Python with it:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Open a fresh terminal window** after the installer finishes so the `uv` command is on your path.

### 2. Download the project

```bash
git clone https://github.com/nanwer/trip-search-mcp.git
cd trip-search-mcp
```

Missing `git`? macOS: run `xcode-select --install`. Windows: install from [git-scm.com](https://git-scm.com/download/win) and reopen PowerShell.

### 3. Install the package

```bash
uv venv
uv pip install -e .
```

Creates `.venv/` and installs everything. About 30 seconds.

### 4. Find the absolute path to the venv Python

You'll paste this into Claude Desktop's config in the next step.

```bash
# macOS / Linux
echo "$(pwd)/.venv/bin/python"
```

```powershell
# Windows
echo "$(Resolve-Path .\.venv\Scripts\python.exe)"
```

Copy what it prints — looks like `/Users/you/trip-search-mcp/.venv/bin/python` (macOS) or `C:\Users\you\trip-search-mcp\.venv\Scripts\python.exe` (Windows).

### 5. Add a `trip-search` entry to Claude Desktop's config

Open the config file:

```bash
# macOS
open -e "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
```

```powershell
# Windows
notepad "$env:APPDATA\Claude\claude_desktop_config.json"
```

If the file doesn't exist:

```bash
# macOS — create then reopen
mkdir -p "$HOME/Library/Application Support/Claude"
echo '{}' > "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
open -e "$HOME/Library/Application Support/Claude/claude_desktop_config.json"
```

```powershell
# Windows — create then reopen
New-Item -ItemType Directory -Path "$env:APPDATA\Claude" -Force | Out-Null
'{"mcpServers": {}}' | Out-File -Encoding utf8 "$env:APPDATA\Claude\claude_desktop_config.json"
notepad "$env:APPDATA\Claude\claude_desktop_config.json"
```

Add a `trip-search` entry inside `mcpServers`. If you already have other MCP servers, place this one alongside them (comma-separated):

```json
{
  "mcpServers": {
    "trip-search": {
      "command": "/PASTE/PATH/FROM/STEP-4/HERE",
      "args": ["-m", "trip_search_mcp.server"]
    }
  }
}
```

**Windows users:** use double backslashes in the `command` path:
```json
"command": "C:\\Users\\you\\trip-search-mcp\\.venv\\Scripts\\python.exe"
```

Save the file.

### 6. Fully quit and reopen Claude Desktop

**Closing the window isn't enough.** Quit from the menu bar (macOS: `⌘Q` or right-click the dock icon → Quit) or from the system tray (Windows: right-click the Claude icon → Quit). Then reopen.

### 7. Test it

Open a new chat in Claude Desktop. Click the hammer/tools icon at the bottom of the message box — you should see `trip-search` with **7 always-on tools** plus 4 more after step 8 below:

| Tool | Needs SERPAPI_KEY? |
|---|---|
| `search_flights` | No |
| `search_cheapest_dates` | No |
| `search_stays` with `category="airbnb"` | No |
| `get_weather_forecast` | No |
| `convert_currency` | No |
| `watch_flight_price` / `list_active_watches` / `cancel_watch` | No |
| `search_stays` (default / hotels / vacation_rentals) | **Yes** |
| `get_stay_details` | **Yes** |
| `search_events` | **Yes** |
| `search_activities` | **Yes** |

Ask Claude:

> *"Find me round-trip flights from JFK to LHR, leaving July 12 returning July 22, 1 adult, economy."*

If you get a summary with prices and a "Book on Google Flights" link, you're done. Browse [FEATURES.md](./FEATURES.md) for everything else you can ask.

---

## Step 8 (optional) — turn on hotel search

Hotels and vacation-rental search use [SerpAPI](https://serpapi.com) — free tier 100 searches/month. The flight tools and the Airbnb category work without it.

1. Sign up at [serpapi.com](https://serpapi.com) (Google login works).
2. Copy your key from [serpapi.com/manage-api-key](https://serpapi.com/manage-api-key).
3. Add an `env` block to your config (step 5):
   ```json
   {
     "mcpServers": {
       "trip-search": {
         "command": "/PASTE/PATH/FROM/STEP-4/HERE",
         "args": ["-m", "trip_search_mcp.server"],
         "env": {
           "SERPAPI_KEY": "paste-your-key-here"
         }
       }
     }
   }
   ```
4. **⌘Q and reopen Claude Desktop.** `search_stays` (hotels/vacation rentals) and `get_stay_details` now work.

---

## If something doesn't work

| Symptom | Fix |
|---|---|
| The `trip-search` server doesn't appear in Claude's tools menu | You forgot to fully quit. ⌘Q (or quit from the system tray on Windows), then reopen. |
| `search_stays` says "SERPAPI_KEY is not set" | The `env` block is missing or you reopened Claude before saving the config. Re-check step 8, then ⌘Q + reopen. |
| Claude says "the tool call timed out" | A previous Claude Desktop quit may have left a stale MCP subprocess running. Run `pgrep -f trip_search_mcp` — if more than 2 PIDs show up, run `pkill -f trip_search_mcp.server` (macOS/Linux) or End Task on every `Claude` process in Task Manager (Windows), then ⌘Q + reopen. |
| `ModuleNotFoundError: No module named 'trip_search_mcp'` | The `command` path in your config points to the wrong Python. Re-run step 4 and paste that exact path. |
| Airbnb search returns an `upstream_error` | Airbnb sometimes pushes back on scraping during high traffic. Wait a few minutes and retry. If it keeps failing, [pyairbnb](https://github.com/johnbalvin/pyairbnb) may need a release. |

[docs/SETUP.md](./docs/SETUP.md) has a longer, verbose walkthrough.

---

## Updating to the latest version

Claude Desktop spawns the MCP subprocess **once** at launch and keeps running it. Pulling new code doesn't reload the running process — you have to ⌘Q and reopen Claude Desktop after every update.

### Recent install (within the last few weeks)

```bash
cd /path/to/trip-search-mcp
git pull
uv pip install -e .          # reinstalls in case dependencies changed
```

Then **⌘Q Claude Desktop and reopen.**

Verify:

```bash
.venv/bin/python -c "from trip_search_mcp.server import mcp; print(mcp.name)"
# → trip-search-mcp
```

### Updating from an older version (before the `flights-mcp` → `trip-search-mcp` rename)

Older installs used the module name `flights_mcp` (now `trip_search_mcp`). If your Claude Desktop config still says `-m flights_mcp.server`, the server will fail to start with `ModuleNotFoundError` after the update. Three things to fix:

1. **Pull and reinstall:**
   ```bash
   cd /path/to/trip-search-mcp     # path is unchanged; GitHub redirects the old repo URL
   git pull
   uv pip install -e .              # picks up new deps including pyairbnb
   ```

2. **Edit your Claude Desktop config.** Open
   `~/Library/Application Support/Claude/claude_desktop_config.json`
   (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows)
   and update the `args` array:

   ```diff
   - "args": ["-m", "flights_mcp.server"]
   + "args": ["-m", "trip_search_mcp.server"]
   ```

   Optionally rename the JSON key from `"flights"` to `"trip-search"`
   so the entry in Claude Desktop's tools menu matches the new docs.

3. **⌘Q Claude Desktop and reopen.**

### Common gotchas during an update

| Symptom | Cause / Fix |
|---|---|
| `ModuleNotFoundError: No module named 'trip_search_mcp'` | Your config still points at the old module name. See "Updating from an older version" above. |
| `ModuleNotFoundError: No module named 'pyairbnb'` | New dependency added since your install. Run `uv pip install -e .` to pick it up. |
| `trip-search` server shows "running" but new tools (`search_stays`, `get_stay_details`, `watch_flight_price`, …) don't appear | You didn't fully quit. Closing the window doesn't kill the subprocess on macOS or Windows. Use ⌘Q (macOS) or the system-tray Quit (Windows). |
| Updates seem to apply but a specific tool times out | Two MCP subprocesses may be running (Claude Desktop occasionally fails to kill the old one). Check with `pgrep -f trip_search_mcp.server` — if you see more than 2 PIDs, run `pkill -f trip_search_mcp.server` and reopen Claude Desktop. |
| The Claude Code CLI (not Desktop) doesn't see the updates | `claude mcp` commands cache server metadata. Restart your Claude Code session, or remove and re-add the server: `claude mcp remove trip-search && claude mcp add trip-search -- /ABSOLUTE/PATH/TO/.venv/bin/python -m trip_search_mcp.server` |

---

## For developers

```bash
.venv/bin/pytest -q          # 350 tests, all fixture-driven, no live API calls
```

Source layout:

```
src/trip_search_mcp/
├── server.py                FastMCP entry point — registers 7 tools
├── models.py                Pydantic I/O models
├── cache.py                 TTL response cache (tool-namespaced keys)
├── cities.py                City code → airport list map (27 cities)
├── errors.py                ErrorCode enum, ToolError, envelope helpers
├── logging_config.py        JSON-line file logger
├── tools/
│   ├── search_flights.py
│   ├── search_cheapest_dates.py
│   ├── search_stays.py
│   ├── get_stay_details.py
│   ├── watch_flight_price.py
│   ├── list_active_watches.py
│   └── cancel_watch.py
├── fli_backend/             flights — via fli library, no auth
├── serpapi_hotels_backend/  hotels + vacation rentals — SerpAPI
├── serpapi_events_backend/  concerts + festivals + sports — SerpAPI google_events
├── tripadvisor_backend/     things-to-do — SerpAPI Tripadvisor (ssrc=A)
├── airbnb_backend/          Airbnb direct — pyairbnb + Nominatim geocoding
├── open_meteo_backend/      weather forecasts — Open-Meteo, no auth
├── ecb_backend/             currency conversion — ECB daily feed, no auth
└── monitoring/              SQLite-backed price watches (lazy refresh)
```

Capture fresh real-data fixtures (uses live APIs — burns 1 call each):

```bash
.venv/bin/python scripts/verify_fli.py                  # flights
.venv/bin/python scripts/verify_serpapi_hotels.py       # hotels
.venv/bin/python scripts/verify_vacation_rentals.py     # rentals
.venv/bin/python scripts/verify_property_details.py     # property details
```

Further docs:

- [FEATURES.md](./FEATURES.md) — every capability, plain English, with example prompts and combined-workflow scenarios.
- [docs/SETUP.md](./docs/SETUP.md) — verbose install + troubleshooting.
- [AGENTS.md](./AGENTS.md) — notes for AI coding agents working on this repo (topology, gotchas, hallucination traps).
- [BACKLOG.md](./BACKLOG.md) — completed items + new follow-ups surfaced during the work.

---

## License

MIT.
