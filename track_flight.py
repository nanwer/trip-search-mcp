"""Standalone terminal price tracker for a single flight route.

Reuses the same `fli` backend the MCP server uses, so prices match Google Flights
exactly. Polls on an interval, logs every snapshot to CSV, and renders a live
Rich dashboard in the terminal.

Usage:
    python track_flight.py --origin KTM --destination BKK \
        --depart 2026-06-15 --return 2026-06-22 --interval 30

Stop with Ctrl+C. CSV log: ./price_log_<origin>_<dest>_<depart>.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import signal
import sys
from datetime import date, datetime
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from trip_search_mcp.fli_backend.client import FliClient
from trip_search_mcp.models import MaxStops, SearchFlightsInput

console = Console()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live terminal flight-price tracker")
    p.add_argument("--origin", required=True)
    p.add_argument("--destination", required=True)
    p.add_argument("--depart", required=True, help="YYYY-MM-DD")
    p.add_argument("--return", dest="ret", default=None, help="YYYY-MM-DD (optional)")
    p.add_argument("--adults", type=int, default=1)
    p.add_argument("--cabin", default="ECONOMY", choices=["ECONOMY", "PREMIUM_ECONOMY", "BUSINESS", "FIRST"])
    p.add_argument("--max-stops", default="ANY", choices=["ANY", "NON_STOP", "ONE_STOP_OR_FEWER", "TWO_OR_FEWER_STOPS"])
    p.add_argument("--interval", type=int, default=30, help="Poll interval in minutes (default 30)")
    p.add_argument("--max-results", type=int, default=10)
    return p.parse_args()


async def fetch_offers(args) -> list:
    client = FliClient()
    params = SearchFlightsInput(
        origin=args.origin.upper(),
        destination=args.destination.upper(),
        departure_date=date.fromisoformat(args.depart),
        return_date=date.fromisoformat(args.ret) if args.ret else None,
        adults=args.adults,
        cabin_class=args.cabin,
        max_stops=MaxStops(args.max_stops),
        max_results=args.max_results,
    )
    return await client.search(params)


def format_offer_row(o) -> tuple[str, str, str, str, str]:
    out = o.outbound
    inb = o.inbound
    out_str = f"{out.segments[0].departure_time_local.strftime('%H:%M')} → {out.segments[-1].arrival_time_local.strftime('%H:%M')} ({out.stops}s, {out.duration})"
    inb_str = (
        f"{inb.segments[0].departure_time_local.strftime('%H:%M')} → {inb.segments[-1].arrival_time_local.strftime('%H:%M')} ({inb.stops}s, {inb.duration})"
        if inb else "—"
    )
    return (
        "/".join(o.airlines),
        out_str,
        inb_str,
        f"{o.total_price:,.0f} {o.currency}",
        out.segments[0].flight_number,
    )


def build_dashboard(args, offers, history, csv_path, last_poll, next_poll, error):
    # Header
    header = Text()
    header.append(f"  {args.origin.upper()} → {args.destination.upper()}  ", style="bold cyan")
    header.append(f"{args.depart}", style="white")
    if args.ret:
        header.append(f" → {args.ret}", style="white")
    header.append(f"  · {args.adults} adult · {args.cabin} · {args.max_stops}\n", style="dim")
    header.append(f"  Last poll: {last_poll}   Next: {next_poll}", style="dim")
    if error:
        header.append(f"\n  ⚠  {error}", style="red")

    # Current offers table
    offers_table = Table(title="Current cheapest offers", title_style="bold", show_lines=False, expand=True)
    offers_table.add_column("Airline", style="cyan", no_wrap=True)
    offers_table.add_column("Outbound")
    offers_table.add_column("Return")
    offers_table.add_column("Price", justify="right", style="green")
    offers_table.add_column("Flight#", style="dim")

    cheapest_now = None
    if offers:
        sorted_offers = sorted(offers, key=lambda o: o.total_price)
        cheapest_now = sorted_offers[0].total_price
        for o in sorted_offers[:8]:
            row = format_offer_row(o)
            style = "bold green" if o.total_price == cheapest_now else ""
            offers_table.add_row(*row, style=style)

    # Price history
    history_table = Table(title=f"Price history ({len(history)} polls)", title_style="bold", expand=True)
    history_table.add_column("Time", style="dim", no_wrap=True)
    history_table.add_column("Cheapest", justify="right")
    history_table.add_column("Δ vs prev", justify="right")
    history_table.add_column("Δ vs first", justify="right")

    first_price = history[0][1] if history else None
    prev_price = None
    for ts, price in history[-15:]:
        delta_prev = ""
        delta_first = ""
        if prev_price is not None:
            d = price - prev_price
            delta_prev = f"{d:+,.0f}" if d else "—"
        if first_price is not None and len(history) > 1:
            d = price - first_price
            delta_first = f"{d:+,.0f}" if d else "—"
        style = ""
        if prev_price is not None:
            if price < prev_price:
                style = "green"
            elif price > prev_price:
                style = "red"
        history_table.add_row(ts.strftime("%m-%d %H:%M"), f"{price:,.0f}", delta_prev, delta_first, style=style)
        prev_price = price

    # Summary stats
    stats = Text()
    if history:
        prices = [p for _, p in history]
        currency = offers[0].currency if offers else ""
        stats.append("  Current: ", style="dim")
        stats.append(f"{cheapest_now:,.0f} {currency}", style="bold green")
        stats.append("   Min: ", style="dim")
        stats.append(f"{min(prices):,.0f}", style="green")
        stats.append("   Max: ", style="dim")
        stats.append(f"{max(prices):,.0f}", style="red")
        stats.append("   Avg: ", style="dim")
        stats.append(f"{sum(prices)/len(prices):,.0f}", style="yellow")
        stats.append(f"\n  Log: {csv_path}", style="dim")

    return Panel(
        Group(header, Text(""), offers_table, Text(""), history_table, Text(""), stats),
        title="✈  Flight Price Tracker  ✈",
        border_style="cyan",
    )


async def main():
    args = parse_args()
    csv_path = Path(f"price_log_{args.origin.upper()}_{args.destination.upper()}_{args.depart}.csv")
    new_file = not csv_path.exists()
    if new_file:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["timestamp", "cheapest_price", "currency", "airline", "flight_number"])

    history: list[tuple[datetime, float]] = []
    # Load existing history if file exists
    if not new_file:
        try:
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    history.append((datetime.fromisoformat(row["timestamp"]), float(row["cheapest_price"])))
        except Exception:
            pass

    stop = asyncio.Event()

    def handle_sig(*_):
        stop.set()

    try:
        signal.signal(signal.SIGINT, handle_sig)
        signal.signal(signal.SIGTERM, handle_sig)
    except Exception:
        pass

    interval_sec = args.interval * 60
    last_poll_str = "—"
    next_poll_str = "—"
    error = None
    offers = []

    with Live(build_dashboard(args, offers, history, csv_path, last_poll_str, next_poll_str, error), refresh_per_second=1, console=console, screen=False) as live:
        while not stop.is_set():
            error = None
            try:
                offers = await fetch_offers(args)
                if offers:
                    cheapest = min(offers, key=lambda o: o.total_price)
                    now = datetime.now()
                    history.append((now, float(cheapest.total_price)))
                    last_poll_str = now.strftime("%Y-%m-%d %H:%M:%S")
                    with csv_path.open("a", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow([
                            now.isoformat(timespec="seconds"),
                            cheapest.total_price,
                            cheapest.currency,
                            "/".join(cheapest.airlines),
                            cheapest.outbound.segments[0].flight_number,
                        ])
                else:
                    error = "No offers returned"
            except Exception as e:
                error = f"Poll failed: {e}"

            # Countdown loop
            remaining = interval_sec
            while remaining > 0 and not stop.is_set():
                mins, secs = divmod(remaining, 60)
                next_poll_str = f"in {mins:02d}m {secs:02d}s"
                live.update(build_dashboard(args, offers, history, csv_path, last_poll_str, next_poll_str, error))
                await asyncio.sleep(1)
                remaining -= 1

    console.print("\n[yellow]Tracker stopped. CSV saved to[/yellow] " + str(csv_path))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
