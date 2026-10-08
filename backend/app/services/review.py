"""Rebuild a daily account ledger from fills, cash transactions and raw closes.

All stored timestamps are naive UTC. A review date ends at midnight in China.
The current date is excluded because its daily bar can still be provisional.
"""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AccountDailySnapshot,
    AccountTransaction,
    DailyReviewNote,
    SimulationAccount,
    Stock,
    StockRawClose,
    Trade,
    TradeNote,
    utcnow,
)
from .market.constants import CHINA_TZ


CENT = Decimal("0.01")


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def local_date(value: datetime) -> date:
    return value.replace(tzinfo=UTC).astimezone(CHINA_TZ).date()


def last_completed_date(now: datetime | None = None) -> date:
    observed = now or datetime.now(UTC)
    return observed.astimezone(CHINA_TZ).date() - timedelta(days=1)


def _serialize(snapshot: AccountDailySnapshot) -> dict:
    return {
        "date": snapshot.snapshot_date,
        "cash": snapshot.cash,
        "market_value": snapshot.market_value,
        "total_assets": snapshot.total_assets,
        "cost_basis": snapshot.cost_basis,
        "realized_pnl": snapshot.realized_pnl,
        "floating_pnl": snapshot.floating_pnl,
        "cash_change": snapshot.cash_change,
        "market_value_change": snapshot.market_value_change,
        "asset_change": snapshot.asset_change,
        "asset_change_delta": snapshot.asset_change_delta,
        "trade_count": snapshot.trade_count,
        "trade_cash_flow": snapshot.trade_cash_flow,
        "fees": snapshot.fees,
        "ledger_cash_delta": snapshot.ledger_cash_delta,
        "ledger_consistent": snapshot.ledger_consistent,
        "reconciliation_delta": snapshot.reconciliation_delta,
        "valuation_status": snapshot.valuation_status,
        "missing_symbols": snapshot.missing_symbols,
        "positions": snapshot.positions,
        "calculated_at": snapshot.calculated_at,
    }


def rebuild_daily_snapshots(
    db: Session,
    account: SimulationAccount,
    *,
    through: date | None = None,
    now: datetime | None = None,
) -> list[AccountDailySnapshot]:
    """Idempotently recalculate all completed days since account creation.

    We never read today's positions or cash for historical dates. Late raw-close
    corrections are incorporated on the next read or background refresh.
    """
    cutoff = last_completed_date(now)
    end = min(through, cutoff) if through else cutoff
    start = local_date(account.created_at)
    if end < start:
        return []
    # Serialize rebuilds for the same account on MySQL. A background refresh
    # and an interactive read must not insert the same unique day twice.
    db.execute(
        select(SimulationAccount.id)
        .where(SimulationAccount.id == account.id)
        .with_for_update()
    ).scalar_one()
    trades = db.scalars(
        select(Trade)
        .where(Trade.account_id == account.id)
        .order_by(Trade.created_at, Trade.id)
    ).all()
    trades_by_day: dict[date, list[Trade]] = defaultdict(list)
    stock_ids: set[int] = set()
    for trade in trades:
        day = local_date(trade.created_at)
        if start <= day <= end:
            trades_by_day[day].append(trade)
            stock_ids.add(trade.stock_id)
    stocks = {
        stock.id: stock
        for stock in db.scalars(select(Stock).where(Stock.id.in_(stock_ids))).all()
    } if stock_ids else {}
    bars_by_stock: dict[int, list[StockRawClose]] = defaultdict(list)
    if stock_ids:
        bars = db.scalars(
            select(StockRawClose)
            .where(StockRawClose.stock_id.in_(stock_ids), StockRawClose.trade_date <= end)
            .order_by(StockRawClose.stock_id, StockRawClose.trade_date)
        ).all()
        for bar in bars:
            bars_by_stock[bar.stock_id].append(bar)
    bar_cursors = {stock_id: 0 for stock_id in stock_ids}
    latest_bars: dict[int, StockRawClose] = {}

    transactions = db.scalars(
        select(AccountTransaction)
        .where(AccountTransaction.account_id == account.id)
        .order_by(AccountTransaction.created_at, AccountTransaction.id)
    ).all()
    transactions_by_day: dict[date, list[AccountTransaction]] = defaultdict(list)
    for transaction in transactions:
        day = local_date(transaction.created_at)
        if start <= day <= end:
            transactions_by_day[day].append(transaction)

    existing = {
        item.snapshot_date: item
        for item in db.scalars(
            select(AccountDailySnapshot).where(
                AccountDailySnapshot.account_id == account.id,
                AccountDailySnapshot.snapshot_date >= start,
                AccountDailySnapshot.snapshot_date <= end,
            )
        ).all()
    }
    cash = Decimal(account.initial_cash)
    ledger_cash = cash
    realized = Decimal("0")
    holdings: dict[int, tuple[int, Decimal]] = {}
    ledger_consistent = True
    previous_cash = cash
    previous_market_value: Decimal | None = Decimal("0")
    previous_assets: Decimal | None = cash
    results: list[AccountDailySnapshot] = []
    day = start
    while day <= end:
        day_trades = trades_by_day[day]
        flow = Decimal("0")
        fees = Decimal("0")
        for trade in day_trades:
            quantity, basis = holdings.get(trade.stock_id, (0, Decimal("0")))
            amount, fee = Decimal(trade.amount), Decimal(trade.fee)
            fees += fee
            if trade.side == "BUY":
                movement = -(amount + fee)
                holdings[trade.stock_id] = (quantity + trade.quantity, basis + amount + fee)
            elif trade.side == "SELL":
                if trade.quantity > quantity or quantity <= 0:
                    raise HTTPException(status_code=409, detail="历史成交数量超过持仓，无法重建账户快照")
                movement = amount - fee
                sold_basis = basis * Decimal(trade.quantity) / Decimal(quantity)
                realized += movement - sold_basis
                remaining = quantity - trade.quantity
                holdings[trade.stock_id] = (remaining, basis - sold_basis) if remaining else (0, Decimal("0"))
            else:
                raise HTTPException(status_code=409, detail="存在未知成交方向，无法重建账户快照")
            cash += movement
            flow += movement

        for transaction in transactions_by_day[day]:
            ledger_cash += Decimal(transaction.amount)
            if cents(ledger_cash) != cents(Decimal(transaction.balance_after)):
                ledger_consistent = False
        if cents(cash - ledger_cash) != 0:
            ledger_consistent = False

        for stock_id, stock_bars in bars_by_stock.items():
            cursor = bar_cursors[stock_id]
            while cursor < len(stock_bars) and stock_bars[cursor].trade_date <= day:
                latest_bars[stock_id] = stock_bars[cursor]
                cursor += 1
            bar_cursors[stock_id] = cursor

        lines: list[dict] = []
        missing: list[str] = []
        carried = False
        provisional = False
        market_value = Decimal("0")
        exact_basis = Decimal("0")
        for stock_id in sorted(holdings, key=lambda item: stocks[item].symbol):
            quantity, basis = holdings[stock_id]
            if not quantity:
                continue
            stock = stocks[stock_id]
            bar = latest_bars.get(stock_id)
            exact_basis += basis
            if bar is None or bar.close_price <= 0:
                missing.append(stock.symbol)
                lines.append({
                    "symbol": stock.symbol, "stock_name": stock.name,
                    "quantity": quantity, "cost_basis": str(cents(basis)),
                    "price": None, "price_date": None, "market_value": None,
                    "price_source": None, "floating_pnl": None,
                })
                continue
            carried |= bar.trade_date < day
            provisional |= bar.source != "HISTORY"
            line_value = cents(Decimal(bar.close_price) * quantity)
            market_value += line_value
            lines.append({
                "symbol": stock.symbol, "stock_name": stock.name,
                "quantity": quantity, "cost_basis": str(cents(basis)),
                "price": str(bar.close_price), "price_date": bar.trade_date.isoformat(),
                "price_source": bar.source,
                "market_value": str(line_value),
                "floating_pnl": str(cents(line_value - basis)),
            })

        status = "MISSING" if missing else "PROVISIONAL" if provisional else "CARRIED" if carried else "COMPLETE"
        assets = cents(cash + market_value) if not missing else None
        realized_display = cents(realized)
        raw_reconciliation = (
            cents(cash + market_value - account.initial_cash - realized - (market_value - exact_basis))
            if assets is not None else None
        )
        # Allocate sub-cent average-cost rounding to floating P&L so the
        # displayed total, realized and floating amounts reconcile exactly.
        floating = assets - cents(account.initial_cash) - realized_display if assets is not None else None
        market_change = (
            cents(market_value - previous_market_value)
            if assets is not None and previous_market_value is not None else None
        )
        asset_change = (
            cents(assets - previous_assets)
            if assets is not None and previous_assets is not None else None
        )
        snapshot = existing.get(day)
        if snapshot is None:
            snapshot = AccountDailySnapshot(account_id=account.id, snapshot_date=day)
            db.add(snapshot)
        snapshot.cash = cents(cash)
        snapshot.market_value = cents(market_value) if assets is not None else None
        snapshot.total_assets = assets
        snapshot.cost_basis = cents(exact_basis)
        snapshot.realized_pnl = realized_display
        snapshot.floating_pnl = floating
        snapshot.cash_change = cents(cash - previous_cash)
        snapshot.market_value_change = market_change
        snapshot.asset_change = asset_change
        snapshot.asset_change_delta = (
            cents(asset_change - snapshot.cash_change - market_change)
            if asset_change is not None and market_change is not None else None
        )
        snapshot.trade_count = len(day_trades)
        snapshot.trade_cash_flow = cents(flow)
        snapshot.fees = cents(fees)
        snapshot.ledger_cash_delta = cents(cash - ledger_cash)
        snapshot.ledger_consistent = ledger_consistent
        snapshot.reconciliation_delta = raw_reconciliation
        snapshot.valuation_status = status
        snapshot.missing_symbols = missing
        snapshot.positions = lines
        snapshot.calculated_at = utcnow()
        results.append(snapshot)
        previous_cash = cash
        previous_market_value = snapshot.market_value
        previous_assets = assets
        day += timedelta(days=1)
    db.commit()
    return results


def snapshot_values(snapshot: AccountDailySnapshot) -> dict:
    return _serialize(snapshot)


def day_review(db: Session, account: SimulationAccount, day: date) -> dict:
    snapshots = rebuild_daily_snapshots(db, account, through=day)
    snapshot = next((item for item in reversed(snapshots) if item.snapshot_date == day), None)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="该日期尚无已完成的账户快照")
    trades = db.scalars(
        select(Trade)
        .where(Trade.account_id == account.id)
        .order_by(Trade.created_at, Trade.id)
    ).all()
    day_trades = [trade for trade in trades if local_date(trade.created_at) == day]
    notes = {
        note.trade_id: note.content
        for note in db.scalars(
            select(TradeNote).where(
                TradeNote.account_id == account.id,
                TradeNote.trade_id.in_([trade.id for trade in day_trades]),
            )
        ).all()
    } if day_trades else {}
    daily_note = db.scalar(
        select(DailyReviewNote).where(
            DailyReviewNote.account_id == account.id,
            DailyReviewNote.review_date == day,
        )
    )
    return {
        "snapshot": _serialize(snapshot),
        "trades": [{
            "trade_no": trade.trade_no,
            "symbol": trade.stock.symbol,
            "stock_name": trade.stock.name,
            "side": trade.side,
            "quantity": trade.quantity,
            "price": trade.price,
            "amount": trade.amount,
            "fee": trade.fee,
            "cash_flow": cents(
                -(trade.amount + trade.fee) if trade.side == "BUY"
                else trade.amount - trade.fee
            ),
            "created_at": trade.created_at,
            "note": notes.get(trade.id),
        } for trade in day_trades],
        "daily_note": daily_note.content if daily_note else None,
    }
