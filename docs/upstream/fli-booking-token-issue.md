# Upstream issue draft: fli booking_token exposure

This is the text we want to file as a feature request on
[punitarani/fli](https://github.com/punitarani/fli/issues). It is NOT
yet filed — Claude's safety classifier blocked an external-repo write
under Nophil's identity. To file it manually:

```bash
gh issue create --repo punitarani/fli \
  --title "Feature: Expose Google Flights booking token (tfs param) for per-offer deep links" \
  --body-file docs/upstream/fli-booking-token-issue.md
```

Or copy-paste the body below into the GitHub web UI.

---

## What

Expose the per-offer booking token that Google Flights encodes in its
`?tfs=` URL parameter. Right now `FlightResult` carries `legs`, `price`,
`currency`, `duration`, `stops` — but no way to construct a URL that
opens the *specific* offer on Google Flights' booking page.

## Why

Downstream tools (notably MCP servers that hand search results to LLMs)
need to give the user a "Book this exact offer" link. Today the best we
can do is link to the generic `/travel/flights?q=...` search URL, which
dumps the user on the results page and forces them to click through to
find "the same offer" by airline + price + times. With the booking
token, we can deep-link straight to `/travel/flights/booking?tfs=<token>`.

## Where it lives in the raw response

Google's internal `TFS` (Travel Flight Search) protobuf encoding includes
a booking token in the per-result entry. fli already parses this response
— the token is present in the bytes, just not surfaced on the
`FlightResult` model.

## Proposal

Add `booking_token: str | None = None` to `FlightResult`. Populate it
from whichever field of the raw response carries the encoded TFS reference
per result. Leave it `None` when fli can't extract it (so existing
consumers don't break).

## Alternative if extraction is hard

Even exposing the raw bytes of the per-result entry (as a base64 string
we can pass back to `https://www.google.com/travel/flights/booking?tfs=<base64>`)
would unblock per-offer deep links for downstream tools.

## Context

Filed by a downstream consumer:
https://github.com/nanwer/trip-search-mcp — an MCP server that wraps
`fli` for Claude. Tracked in our backlog as item #1 (booking URL
deep-linking, flights).
