# Phase 0 + Phase 1 Verification Guide

Walks you through everything Claude couldn't do automatically: signing up for
Amadeus, capturing a real fixture, and running the MCP Inspector check against
the live server.

You'll do this once. After it works, Phase 1 is genuinely shippable.

---

## Part A — Amadeus signup and fixture capture

### Step 1: Create a free Amadeus Self-Service account

1. Open <https://developers.amadeus.com> in your browser.
2. Click **Register** (top right).
3. Fill in name, email, password. Verify your email when the confirmation lands.

No business info is required for Self-Service. The free tier gives 2,000 calls
per month, which is far more than you'll need for development.

### Step 2: Create a Self-Service app to get API credentials

1. After logging in, click **My Self-Service Workspace** (top right menu).
2. Click **Create New App**.
3. Name it anything (e.g. `Flights MCP Phase 1`). Leave the description blank.
4. Submit.

On the app's detail page you'll see two strings:

- **API Key** — copy this into your `.env` as `AMADEUS_CLIENT_ID`
- **API Secret** — copy this into your `.env` as `AMADEUS_CLIENT_SECRET`

Both are ~30+ characters. Treat the secret like a password.

### Step 3: Wire the credentials into `.env`

From the project root:

```bash
cd "/Users/nophilanwer/Personal Projects/Flight api"
cp .env.example .env
```

Open `.env` in your editor and fill in the two values:

```
AMADEUS_CLIENT_ID=<paste API Key here>
AMADEUS_CLIENT_SECRET=<paste API Secret here>
AMADEUS_ENV=test
```

Leave the other lines commented out — the defaults work.

### Step 4: Capture the real fixture

There's a helper script that handles the OAuth dance and saves the response:

```bash
set -a; source .env; set +a
.venv/bin/python scripts/fetch_phase0_fixture.py
```

What you'll see:

```
→ POST https://test.api.amadeus.com/v1/security/oauth2/token
  ✓ token acquired (xxxxxxxx…)
→ GET https://test.api.amadeus.com/v2/shopping/flight-offers (HEL→IAD)
  ✓ N offers returned
  ✓ saved to tests/fixtures/hel_iad_round_trip.json
Time-format spot-check:  departure.at = '2026-05-18T15:30:00'
  ✓ Matches the spec (no offset, local airport time)
```

#### If the script says "Zero offers"

That means HEL→IAD isn't in Amadeus's test cache (a known limitation of the
test environment). Try a known-good route:

```bash
.venv/bin/python scripts/fetch_phase0_fixture.py --origin MAD --destination FRA
```

Other pairs that are usually cached: `LHR→CDG`, `JFK→LAX`, `FRA→MUC`, `BCN→LIS`.

The fixture-driven test suite doesn't care which route you used — it only cares
that the response is structurally a valid Amadeus offer.

#### If the script says the times include a timezone offset

The spec assumes Amadeus returns `at` values like `"2026-05-18T15:30:00"`
(no offset). If your real fixture has `"2026-05-18T15:30:00+03:00"` or
`"…Z"`, tell me — we'll need a small adjustment to `normalize.py` and the
time-format regression test.

### Step 5: Re-run the test suite to confirm everything still works

```bash
.venv/bin/pytest -v
```

Expect 64 passing. The new fixture sits alongside `synthetic_round_trip.json`
and isn't loaded by any existing test, so it can't break the suite — it's there
for your manual inspection and for any future test that wants the real shape.

---

## Part B — Live verification with MCP Inspector

This is the end-of-Phase-1 acceptance check. You'll talk to the running MCP
server through Anthropic's official Inspector UI.

### Prerequisites

- **Node.js** — already installed on your machine (v22).
- **`.env`** populated from Part A.

### Step 1: Start the Inspector

In a fresh terminal:

```bash
cd "/Users/nophilanwer/Personal Projects/Flight api"
set -a; source .env; set +a
npx @modelcontextprotocol/inspector .venv/bin/python -m flights_mcp.server
```

First-time `npx` will prompt to install the Inspector package — say yes (~10s).

It'll print something like:

```
🔍 MCP Inspector is up and running at http://127.0.0.1:6274 🚀
```

It opens a browser tab automatically. If it doesn't, paste that URL.

### Step 2: Connect to the server

The Inspector UI shows the MCP server it just launched as a "stdio" connection.
You should see a green "Connected" indicator and a sidebar with sections like
**Tools**, **Resources**, **Prompts**.

If it shows red/disconnected, check the terminal where `npx` is running — the
server's logs go there. Most likely failure: missing/wrong Amadeus credentials,
which raises a `RuntimeError` at startup with a clear message.

### Step 3: Verify the tool is registered

1. Click the **Tools** tab in the sidebar.
2. You should see one tool: **search_flights**.
3. Click it. The right panel shows the full description (the prompt-engineering
   text — multi-paragraph, including the cache-TTL note and the
   "`baggage_allowance` null doesn't mean no bag" warning).

If the description is wrong or empty, something regressed — check
`src/flights_mcp/tools/search_flights.py:24`.

### Step 4: Make a live search call

Fill in the form on the Tools panel:

| Field            | Value         |
|------------------|---------------|
| origin           | `HEL`         |
| destination      | `IAD`         |
| departure_date   | `2026-05-18`  |
| return_date      | `2026-05-29`  |
| adults           | `1`           |

(Use whatever route Part A confirmed was in the cache, if HEL→IAD wasn't.)

Click **Run Tool**.

**Expected result:**

```json
{
  "results": [
    {
      "offer_id": "1",
      "total_price": 742.18,
      "currency": "USD",
      "airlines": ["AY", "AA"],
      ...
    },
    ...
  ]
}
```

If you instead see:

```json
{"error": {"code": "no_results", "message": "...test environment..."}}
```

…then the route isn't in the test cache. Try the route Part A worked with.

### Step 5: Exercise the error paths

**Invalid input** — change `origin` to `hel` (lowercase) and run:
```json
{"error": {"code": "invalid_input", "message": "Invalid input on 'origin': ..."}}
```

**Past date** — change `departure_date` to `2024-01-01` and run:
```json
{"error": {"code": "invalid_input", "message": "Invalid input on 'departure_date': ..."}}
```

**No results** — try `origin=INV`, `destination=KUO` (two real but unconnected
Finnish airports):
```json
{"error": {"code": "no_results", "message": "...test environment..."}}
```

### Step 6: Verify caching

1. Run the same successful query twice in a row (same origin/destination/dates).
2. Open the log file:
   ```bash
   tail -n 20 ~/.flights-mcp/logs/flight-search.log
   ```
3. The second call should produce a `"msg": "tool.cache_hit"` line, and the
   second call should return faster than the first (no Amadeus round-trip).

### Step 7: Shut down

Ctrl+C in the terminal where `npx` is running.

---

## Phase 1 done checklist

Tick these off as you go:

- [ ] Amadeus Self-Service account created (Part A, Step 1)
- [ ] API Key + Secret obtained (Part A, Step 2)
- [ ] `.env` populated (Part A, Step 3)
- [ ] Real fixture saved with N≥1 offers (Part A, Step 4)
- [ ] `pytest` still green after fixture capture (Part A, Step 5)
- [ ] Inspector connects to the server (Part B, Step 2)
- [ ] `search_flights` tool shows in the Inspector with full description (Part B, Step 3)
- [ ] Live search returns offers (Part B, Step 4)
- [ ] `invalid_input` error envelope works (Part B, Step 5)
- [ ] `no_results` error envelope works (Part B, Step 5)
- [ ] Cache hit logged on identical second call (Part B, Step 6)

When all eleven boxes are ticked, Phase 1 is genuinely done. Phase 2 (HTTP
transport + Cloudflare Tunnel) is the next thing to plan.

---

## Common problems and fixes

**`npx` hangs forever after "MCP Inspector is up"** — that's normal. It runs
until you Ctrl+C. The work happens in the browser tab.

**Inspector says "Connection lost"** — the server crashed. Check the `npx`
terminal for the Python traceback. Most likely a missing env var.

**`AuthlibDeprecationWarning`** — cosmetic FastMCP warning, ignore.

**Search returns weird data with timestamps that have `+03:00`** — your real
fixture has timezone offsets. Tell me and we'll patch `normalize.py` to handle
both shapes.

**You hit "monthly quota exceeded"** — you've burned through 2,000 calls.
Either wait until next month, apply for production access, or rely entirely on
the synthetic fixture for development.
