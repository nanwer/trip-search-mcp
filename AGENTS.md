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
  Bluepillow.com). If a user asks for "Airbnb" specifically, explain
  the limitation rather than promising it.

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
