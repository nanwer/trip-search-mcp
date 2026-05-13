# Agent / Claude Code instructions

Read this first. Re-read it whenever a previous session gave wrong advice
— there's a high chance the same mistake is repeating.

## Deployment topology (CRITICAL — sessions have gotten this wrong)

There is **only one** instance of this MCP server. It runs as a **local
stdio subprocess** that Claude Desktop spawns on Nophil's Mac, configured
in `~/Library/Application Support/Claude/claude_desktop_config.json`.

There is **NO remote server**. There is **NO home server**. There is **NO
Cloudflare Tunnel**. There is **NO HTTP transport**. HTTP transport was
listed as a future Phase 2-ish item in the original SPEC.md, but it was
never built — the project shipped stdio-only and has stayed stdio-only.

claude.ai web sessions CAN call this tool. They reach the local Claude
Desktop subprocess through Anthropic's account-level MCP bridge — when
you're logged into the same account on both claude.ai and Claude Desktop,
the web client can invoke tools registered locally. **There is still only
one server process, and it lives inside Claude Desktop on the Mac.**

## How to fix "the tool call timed out / behaves stale" from EITHER client

The fix is the same regardless of whether the failing call came from
Claude Desktop, Claude Code, or claude.ai web:

1. **⌘Q Claude Desktop** (full quit; closing the window doesn't kill the
   subprocess).
2. **Reopen Claude Desktop.**
3. Retry from whichever client you were using.

After the restart, sanity-check the subprocess is running on current code:

```bash
ps -o lstart=,command= -p $(pgrep -f trip_search_mcp.server | head -1)
```

The start time should be recent. If it's older than the latest commit
that fixed your issue, the restart didn't take — force-quit Claude
through Activity Monitor and reopen.

## Past hallucinations to NOT repeat

A prior claude.ai session diagnosed a timeout as "the remote server on
your home server reached via Cloudflare Tunnel is on stale code." That
diagnosis was **invented out of whole cloth** — there is no such
deployment. Don't suggest:

- SSH'ing to a home server
- Checking Cloudflare Tunnel health
- Restarting a systemd unit / Docker container / pm2 process
- `git pull` on a remote
- Any "remote restart" of any kind

The only restartable thing in this project is the local Claude Desktop
subprocess, and ⌘Q + reopen is how you restart it.

## When in doubt: ask the user before inventing infrastructure

If a session genuinely doesn't know how the user is reaching the tool,
ask. Don't guess. "Are you using Claude Desktop, Claude Code CLI, or
claude.ai web?" is a fine question. Inventing a remote deployment that
doesn't exist wastes the user's time and breeds wrong fixes.

## Code-quality conventions worth honoring

- Tests are fixture-driven. No live API calls in the suite.
- The injectable-searcher pattern (`FliClient` for flights,
  `httpx.MockTransport` for `SerpAPIHotelsClient`) is intentional — keep
  it. Tests substitute mocks.
- Tool descriptions are the LLM-facing contract. Update them when you
  change input/output shapes or filter semantics.
- Cache keys are namespaced by tool name (`{"tool": TOOL_NAME, ...}`).
  Add the prefix when introducing a new tool.
- The stays tool is OPT-IN via SERPAPI_KEY. `search_stays` checks at
  call time whether the stays client was configured; if not, returns a
  structured `auth_failed` envelope. The SERVER does NOT require
  SERPAPI_KEY at startup — flights staying key-free is a deliberate
  product property. Don't change that.
- `search_stays(category="all")` makes **2 SerpAPI calls per query**
  (one for hotels, one for vacation rentals). This is intentional and
  doubles quota burn vs `category="hotels"` or `"vacation_rentals"`.
  100/month free tier covers ~50 merged queries. Don't change the
  default without thinking through the user's quota budget.
- SerpAPI returns HTTP 400 if you send `hotel_class` with
  `vacation_rentals=true` or `bedrooms`/`bathrooms` with
  `vacation_rentals=false`. The client's `_build_query` keeps these
  scoped per mode. There's a regression test at
  `test_stays_merge.py::test_min_bedrooms_routed_only_to_rentals_request`.
- Google's vacation-rental aggregation does NOT include Airbnb. The
  `sources` field surfaces OTAs (Booking.com, Hotels.com, Vrbo.com,
  Bluepillow.com). **For Airbnb specifically, use `category="airbnb"`
  on `search_stays`** — it bypasses SerpAPI and hits Airbnb directly
  via the pyairbnb library. Default `category="all"` continues to use
  SerpAPI only (Airbnb is opt-in to avoid degrading the common case
  with pyairbnb's higher fragility).
- City codes (`WAS`, `NYC`, `LON`, …) work on `search_flights` and
  `search_cheapest_dates` — the tools auto-expand them to constituent
  airports (capped at 3 per side) and fan out in parallel. The map
  lives in `src/trip_search_mcp/cities.py`; adding cities is a
  one-line edit.
- The deal-hunting tools (`watch_flight_price`, `list_active_watches`,
  `cancel_watch`) persist to SQLite at
  `~/.trip-search-mcp/watches.db`. **No background daemon** —
  `list_active_watches` lazy-refreshes any watch whose latest check is
  older than `refresh_after_hours` (default 6h). If you find yourself
  wanting an always-on scheduler, see BACKLOG.md #8 — it's a
  deliberate non-feature for now because the MCP server isn't
  always-on either.
- `get_stay_details` is the property-details follow-up tool. It costs
  1 SerpAPI quota per call (NOT cached across `(token, dates)` tuples
  for very long — TTLCache default 5 min). Use it sparingly; surface
  the `booking_partners` array prominently in card rendering since
  those are the direct booking-flow links.
- **City-expansion fanout is SERIAL, not parallel.** Earlier code used
  `asyncio.gather` to fan out `WAS → IAD/DCA/BWI` searches in parallel.
  That triggered Google's rate limiter, fli retried with backoff on
  every pair, and search latency blew up to 150+ seconds per query.
  The current code calls each pair sequentially — bounded by
  `MAX_AIRPORTS_PER_SIDE × MAX_AIRPORTS_PER_SIDE = 9` worst case at
  ~5-10s each = ~50-90s ceiling. If you ever want parallel fanout
  back, gate it with `asyncio.Semaphore(1)` first and measure.
- **All sqlite3 calls in the monitoring layer go through
  `asyncio.to_thread`.** Calling sync sqlite from an async function
  technically works but blocks the event loop. After a heavy fli
  fanout (which uses the default thread executor), sync sqlite calls
  can deadlock-look in production for 4+ minutes. Don't unwrap them.
- **Stale subprocess gotcha:** Claude Desktop occasionally fails to
  kill the old MCP subprocess on ⌘Q + reopen, leaving two subprocesses
  running. Symptom: tools return correctly via one path but time out
  via another. Diagnostic: `pgrep -f trip_search_mcp.server` — should
  be exactly 2 PIDs (the disclaimer wrapper + the actual Python).
  Fix: `pkill -f trip_search_mcp.server` then ⌘Q + reopen.

## Useful repo entry points

- `SEARCH-STAYS-SPEC.md` — phase plan for the unified stays tool (all
  phases shipped).
- `MIGRATION-FLI-SPEC.md` — phased flights migration plan (Phase 1 + 2
  + 2.5 done).
- `docs/SETUP.md` — install + connect walkthrough for end users.
- `scripts/verify_fli.py` — capture fresh fli fixtures.
- `scripts/verify_serpapi_hotels.py` — capture fresh hotel fixture.
- `scripts/verify_vacation_rentals.py` — capture fresh vacation-rentals
  fixture + compare against the hotels response (used for Phase 0 of
  the search_stays work).
- The latest behavior-affecting change shipped with the search_stays
  rollout: `search_hotels` is GONE, replaced by `search_stays` with a
  `category` dispatcher. If a Desktop subprocess predates this commit,
  it's still running search_hotels and the user should ⌘Q + reopen
  (AND update their config — `-m trip_search_mcp.server` is unchanged,
  but the tool name in the menu changed).
