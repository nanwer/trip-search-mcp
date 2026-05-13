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
ps -o lstart=,command= -p $(pgrep -f flights_mcp.server | head -1)
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
- The hotels tool is OPT-IN via SERPAPI_KEY. `search_hotels` checks at
  call time whether the hotels client was configured; if not, returns a
  structured `auth_failed` envelope. The SERVER does NOT require
  SERPAPI_KEY at startup — flights staying key-free is a deliberate
  product property. Don't change that.

## Useful repo entry points

- `MIGRATION-FLI-SPEC.md` — phased migration plan (Phase 1 + 2 + 2.5 done).
- `docs/SETUP.md` — install + connect walkthrough for end users.
- `scripts/verify_fli.py` — capture fresh real-data fixtures (one live
  SearchFlights + one SearchDates call).
- The latest behavior-affecting change shipped in commit `a3791e7`
  (Phase 2.5: exclusive-end window semantics, airlines wording, offer_id
  collision fix). If a Desktop subprocess predates this commit, it's
  running pre-2.5 behavior and the user should ⌘Q + reopen.
