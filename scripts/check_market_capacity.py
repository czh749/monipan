"""One bounded real-source sample, without changing stocks or user accounts."""

import argparse
from dataclasses import asdict
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols-file", type=Path, required=True, help="JSON array from a verified catalog snapshot")
    parser.add_argument("--size", type=int, choices=(200, 300, 800), default=800)
    parser.add_argument("--history-samples", type=int, choices=(0, 1, 3), default=3)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    from app.services import market
    from app.services.market.history import fetch_eastmoney_history
    from app.services.market.session import market_session
    from app.stock_pool import STOCK_POOL
    catalog = json.loads(args.symbols_file.read_text(encoding="utf-8"))
    if not isinstance(catalog, list) or any(not isinstance(s, str) or not re.fullmatch(r"[036]\d{5}", s) for s in catalog):
        parser.error("Expected supported six-digit Shanghai/Shenzhen symbols")
    default = [row[0] for row in STOCK_POOL]
    outside = sorted(set(catalog) - set(default))
    symbols = list(dict.fromkeys(default + outside))[:args.size]
    if len(symbols) != args.size:
        parser.error("Catalog has too few unique stocks")
    started = time.monotonic()
    report = {"observed_at": datetime.now(UTC).isoformat(), "scope": "one real-source round, no database writes",
              "requested": len(symbols), "session": market_session()[0], "quote_pass": False,
              "history_pass": False, "history": []}
    try:
        quotes = market.fetch_eastmoney_quotes(symbols)
        valid = {s: q for s, q in quotes.items() if q["price"] > 0 and q["prev_close"] > 0
                 and q.get("quote_source_at") and q.get("quote_trade_date")}
        report.update(available=len(valid), coverage_percent=round(100 * len(valid) / len(symbols), 2),
                      missing=sorted(set(symbols) - valid.keys()), batch_stats=asdict(quotes.stats),
                      trade_dates=sorted({str(q["quote_trade_date"]) for q in valid.values()}))
        report["quote_pass"] = len(valid) / len(symbols) >= 0.95 and quotes.stats.elapsed_seconds <= 240
    except market.MarketDataError as error:
        report.update(available=0, coverage_percent=0, error_type=type(error).__name__,
                      batch_stats=asdict(error.batch_stats) if error.batch_stats else None)
    for symbol in outside[:args.history_samples]:
        item = {"symbol": symbol}
        try:
            bars = fetch_eastmoney_history(symbol, 90)
            item.update(bars=len(bars), passed=len(bars) >= 50)
        except market.MarketDataError as error:
            item.update(bars=0, passed=False, error_type=type(error).__name__)
        report["history"].append(item)
    report["history_pass"] = bool(report["history"]) and all(x["passed"] for x in report["history"])
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    report["status"] = "passed" if report["quote_pass"] and report["history_pass"] else "failed"
    report["limits"] = ["Not a sustained intraday capacity test", "Does not verify production network or AI calls"]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
