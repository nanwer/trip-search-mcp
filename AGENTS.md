# Agent / Claude Code instructions

Read this first. Re-read it whenever you've been told a previous session
gave wrong advice — there's a high chance the same issue is repeating.

## Deployment topology (CRITICAL — past sessions have gotten this wrong)

This MCP server runs in **two separate places**:

1. **Local stdio under Claude Desktop on Nophil's Mac.** Spawned as a
   subprocess of Claude Desktop using the config in
   `~/Library/Application Support/Claude/claude_desktop_config.json`. Used
   for testing and dev. Restart this by ⌘Q + reopening Claude Desktop.

2. **Remote HTTP server on Nophil's home server, reached by claude.ai web
   via Cloudflare Tunnel.** This is what the deferred `mcp__flights__*`
   tools in claude.ai sessions actually hit. **It is NOT Claude Desktop.**
   Restarting Claude Desktop does NOTHING for this path.

**When a tool call from claude.ai web times out or behaves like it's on
stale code, the answer is to restart the REMOTE server**, not Claude
Desktop. Specifically:

- SSH (or otherwise connect) to the home server
- `git pull` to get the latest commit
- Restart the service running `python -m flights_mcp.server` (or whatever
  process manager is in front of it — systemd, Docker, screen, pm2, etc.)
- Confirm the tunnel is still reachable from the public side

The local CLI test path (`.venv/bin/python -c "..."` against
`flights_mcp.server`) verifies the CODE is correct. It does NOT verify the
remote deployment. A green local test + a 4-minute claude.ai timeout means
the remote is on stale code or down.

## Things to never suggest when claude.ai web is failing

- "Restart Claude Desktop" — that's a different server.
- "Update `claude_desktop_config.json`" — that controls Desktop, not the
  remote.
- "Quit and reopen the Claude Desktop app" — same problem.

## Things to suggest

- Pull latest, restart the remote service.
- Check tunnel health: `curl -I https://<your-mcp-subdomain>/` or equivalent.
- Tail the remote server's logs (where the JSON-line logger writes;
  default `~/.flights-mcp/logs/flight-search.log` on the deployment host).
- If the remote auto-pulls on a schedule (cron / GitHub Actions deploy
  hook / Watchtower for Docker), confirm the last pull included the
  expected commit.

## Code-quality conventions worth honoring

- Tests are fixture-driven. No live API calls in the suite.
- The injectable-searcher pattern for `FliClient` is intentional — keep
  it. Tests substitute `_MockSearcher` instances.
- Tool descriptions are the LLM-facing contract. Update them when you
  change input/output shapes or filter semantics.
- Cache keys are namespaced by tool name (`{"tool": TOOL_NAME, ...}`).
  Add the prefix when introducing a new tool.

## Useful repo entry points

- `MIGRATION-FLI-SPEC.md` — phased migration plan (Phase 1 + 2 done,
  Phase 2.5 done). Phase 3 cleanup notes live there too.
- `docs/SETUP.md` — install + connect walkthrough for end users.
- `scripts/verify_fli.py` — capture fresh real-data fixtures (one live
  SearchFlights + one SearchDates call).
- Test commit `a3791e7` shipped Phase 2.5 (exclusive-end window semantics,
  airlines wording fix, offer_id collision fix). If the remote is older
  than this, the live behavior won't match the README.
