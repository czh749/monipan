"""Read-only portfolio snapshots and deterministic risk calculations."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..models import Position, SimulationAccount
from ..services.market import stock_history
from ..services.trading import position_sellable_values


@dataclass(frozen=True)
class PortfolioResult:
    status: str
    as_of: datetime | None
    data: dict[str, Any]
    evidence: list[dict[str, Any]]
    warnings: list[str]


class PortfolioDataError(RuntimeError):
    pass


def _account(db: Session, account_id: int) -> SimulationAccount:
    account = db.scalar(
        select(SimulationAccount)
        .where(SimulationAccount.id == account_id)
        .options(joinedload(SimulationAccount.user))
    )
    if account is None:
        raise PortfolioDataError("模拟账户不存在")
    return account


def _positions(db: Session, account_id: int) -> list[Position]:
    return list(
        db.scalars(
            select(Position)
            .where(Position.account_id == account_id, Position.quantity > 0)
            .options(joinedload(Position.stock))
            .order_by(Position.updated_at.desc())
        ).all()
    )


def _position_values(
    db: Session,
    account: SimulationAccount,
    position: Position,
) -> dict[str, Any]:
    market_value = position.stock.price * position.quantity
    cost_value = position.average_cost * position.quantity
    profit_loss = market_value - cost_value
    profit_loss_percent = (
        profit_loss / cost_value * Decimal("100") if cost_value else Decimal("0")
    )
    sellable, frozen, orderable = position_sellable_values(
        db,
        account.id,
        position,
    )
    return {
        "symbol": position.stock.symbol,
        "stock_name": position.stock.name,
        "exchange": position.stock.exchange,
        "industry": position.stock.industry,
        "quantity": position.quantity,
        "sellable_quantity": sellable,
        "frozen_sell_quantity": frozen,
        "orderable_quantity": orderable,
        "average_cost": position.average_cost,
        "current_price": position.stock.price,
        "market_value": market_value,
        "cost_value": cost_value,
        "profit_loss": profit_loss,
        "profit_loss_percent": profit_loss_percent,
        "quote_updated_at": position.stock.updated_at,
    }


def portfolio_snapshot(db: Session, account_id: int) -> PortfolioResult:
    account = _account(db, account_id)
    positions = _positions(db, account_id)
    values = [_position_values(db, account, position) for position in positions]
    market_value = sum(
        (item["market_value"] for item in values),
        Decimal("0"),
    )
    total_assets = account.available_cash + market_value
    total_profit_loss = total_assets - account.initial_cash
    total_return_percent = (
        total_profit_loss / account.initial_cash * Decimal("100")
        if account.initial_cash
        else Decimal("0")
    )
    for item in values:
        item["asset_weight_percent"] = (
            item["market_value"] / total_assets * Decimal("100")
            if total_assets
            else Decimal("0")
        )
    warnings = [
        f"{item['symbol']} 尚未取得有效行情"
        for item in values
        if item["current_price"] <= 0
    ]
    as_of = max(
        (item["quote_updated_at"] for item in values),
        default=None,
    )
    evidence = [
        {
            "evidence_type": "POSITION_QUOTE",
            "symbol": item["symbol"],
            "quote_updated_at": item["quote_updated_at"],
            "source": "MONIPAN_MARKET_CACHE",
        }
        for item in values
    ]
    return PortfolioResult(
        status="PARTIAL" if warnings else "OK",
        as_of=as_of,
        data={
            "username": account.user.username,
            "initial_cash": account.initial_cash,
            "available_cash": account.available_cash,
            "market_value": market_value,
            "total_assets": total_assets,
            "total_profit_loss": total_profit_loss,
            "total_return_percent": total_return_percent,
            "position_count": len(values),
            "positions": values,
        },
        evidence=evidence,
        warnings=warnings,
    )


def _returns_by_date(bars: list[Any]) -> dict[date, float]:
    result: dict[date, float] = {}
    for previous, current in zip(bars, bars[1:]):
        previous_close = float(previous.close_price)
        if previous_close <= 0:
            continue
        result[current.trade_date] = float(current.close_price) / previous_close - 1.0
    return result


def _sample_covariance(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    return sum(
        (left_value - left_mean) * (right_value - right_mean)
        for left_value, right_value in zip(left, right)
    ) / (len(left) - 1)


def _max_drawdown(returns: list[float]) -> float:
    wealth = 1.0
    peak = 1.0
    maximum = 0.0
    for daily_return in returns:
        wealth *= 1.0 + daily_return
        peak = max(peak, wealth)
        if peak > 0:
            maximum = max(maximum, (peak - wealth) / peak)
    return maximum


def _risk_flags(
    *,
    largest_weight: float,
    top_three_weight: float,
    largest_industry_weight: float,
    annualized_volatility: float | None,
    maximum_drawdown: float | None,
    missing_ratio: float,
) -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    if largest_weight > 0.30:
        flags.append({"level": "HIGH", "code": "SINGLE_STOCK_CONCENTRATION", "message": "最大单股资产占比超过30%"})
    if top_three_weight > 0.70:
        flags.append({"level": "HIGH", "code": "TOP3_CONCENTRATION", "message": "前三大持仓资产占比超过70%"})
    if largest_industry_weight > 0.50:
        flags.append({"level": "MEDIUM", "code": "INDUSTRY_CONCENTRATION", "message": "最大行业资产占比超过50%"})
    if annualized_volatility is not None and annualized_volatility > 0.35:
        flags.append({"level": "HIGH", "code": "HIGH_VOLATILITY", "message": "历史年化波动率超过35%"})
    if maximum_drawdown is not None and maximum_drawdown > 0.20:
        flags.append({"level": "HIGH", "code": "LARGE_DRAWDOWN", "message": "样本期组合最大回撤超过20%"})
    if missing_ratio > 0:
        flags.append({"level": "DATA", "code": "INCOMPLETE_HISTORY", "message": "部分持仓历史行情不足，风险结果不完整"})
    return flags


def calculate_portfolio_risk_values(
    db: Session,
    account_id: int,
    *,
    history_days: int,
) -> PortfolioResult:
    account = _account(db, account_id)
    positions = _positions(db, account_id)
    position_values = [_position_values(db, account, item) for item in positions]
    market_value = sum(
        (item["market_value"] for item in position_values),
        Decimal("0"),
    )
    total_assets = account.available_cash + market_value
    if total_assets <= 0:
        raise PortfolioDataError("账户总资产无效")

    weights = {
        item["symbol"]: float(item["market_value"] / total_assets)
        for item in position_values
    }
    sorted_weights = sorted(weights.values(), reverse=True)
    industry_weights: dict[str, float] = {}
    for item in position_values:
        industry_weights[item["industry"]] = (
            industry_weights.get(item["industry"], 0.0)
            + weights[item["symbol"]]
        )

    return_maps: dict[str, dict[date, float]] = {}
    history_sources: dict[str, str] = {}
    history_ranges: dict[str, dict[str, Any]] = {}
    individual_volatility: dict[str, float | None] = {}
    warnings: list[str] = []
    for position in positions:
        source, bars = stock_history(db, position.stock, history_days)
        history_sources[position.stock.symbol] = source
        history_ranges[position.stock.symbol] = {
            "start": bars[0].trade_date if bars else None,
            "end": bars[-1].trade_date if bars else None,
            "bar_count": len(bars),
        }
        returns = _returns_by_date(bars)
        if len(returns) < 20:
            individual_volatility[position.stock.symbol] = None
            warnings.append(f"{position.stock.symbol} 有效日收益不足20个")
            continue
        return_maps[position.stock.symbol] = returns
        individual_volatility[position.stock.symbol] = (
            statistics.stdev(returns.values()) * math.sqrt(252)
        )

    valid_symbols = list(return_maps)
    common_dates: list[date] = []
    if valid_symbols:
        common = set(return_maps[valid_symbols[0]])
        for symbol in valid_symbols[1:]:
            common &= set(return_maps[symbol])
        common_dates = sorted(common)

    annualized_volatility: float | None = None
    maximum_drawdown: float | None = None
    volatility_contribution: dict[str, float | None] = {
        item["symbol"]: None for item in position_values
    }
    if len(common_dates) >= 20:
        series = {
            symbol: [return_maps[symbol][day] for day in common_dates]
            for symbol in valid_symbols
        }
        covariance = {
            (left, right): _sample_covariance(series[left], series[right])
            for left in valid_symbols
            for right in valid_symbols
        }
        portfolio_variance = sum(
            weights[left] * weights[right] * covariance[(left, right)]
            for left in valid_symbols
            for right in valid_symbols
        )
        if portfolio_variance > 0:
            annualized_volatility = math.sqrt(portfolio_variance * 252)
            for symbol in valid_symbols:
                covariance_with_portfolio = sum(
                    covariance[(symbol, other)] * weights[other]
                    for other in valid_symbols
                )
                volatility_contribution[symbol] = (
                    weights[symbol]
                    * covariance_with_portfolio
                    / portfolio_variance
                )
        portfolio_returns = [
            sum(
                weights[symbol] * return_maps[symbol][day]
                for symbol in valid_symbols
            )
            for day in common_dates
        ]
        maximum_drawdown = _max_drawdown(portfolio_returns)
    elif positions:
        warnings.append("持仓共同交易日期不足20个，无法可靠计算组合波动和回撤")

    missing_count = len(positions) - len(valid_symbols)
    missing_ratio = missing_count / len(positions) if positions else 0.0
    largest_weight = sorted_weights[0] if sorted_weights else 0.0
    top_three_weight = sum(sorted_weights[:3])
    largest_industry_weight = max(industry_weights.values(), default=0.0)
    flags = _risk_flags(
        largest_weight=largest_weight,
        top_three_weight=top_three_weight,
        largest_industry_weight=largest_industry_weight,
        annualized_volatility=annualized_volatility,
        maximum_drawdown=maximum_drawdown,
        missing_ratio=missing_ratio,
    )

    per_position = []
    for item in position_values:
        symbol = item["symbol"]
        per_position.append(
            {
                "symbol": symbol,
                "stock_name": item["stock_name"],
                "industry": item["industry"],
                "asset_weight_percent": weights[symbol] * 100,
                "annualized_volatility_percent": (
                    individual_volatility[symbol] * 100
                    if individual_volatility.get(symbol) is not None
                    else None
                ),
                "portfolio_volatility_contribution_percent": (
                    volatility_contribution[symbol] * 100
                    if volatility_contribution.get(symbol) is not None
                    else None
                ),
                "history_source": history_sources.get(symbol),
                "history_range": history_ranges.get(symbol),
            }
        )

    evidence = [
        {
            "evidence_type": "STOCK_HISTORY",
            "symbol": item["symbol"],
            "source": item["history_source"],
            **(item["history_range"] or {}),
        }
        for item in per_position
    ]
    as_of = max(
        (item["quote_updated_at"] for item in position_values),
        default=None,
    )
    return PortfolioResult(
        status="PARTIAL" if warnings else "OK",
        as_of=as_of,
        data={
            "history_days_requested": history_days,
            "common_return_days": len(common_dates),
            "position_count": len(positions),
            "total_assets": total_assets,
            "cash_weight_percent": float(account.available_cash / total_assets * 100),
            "largest_position_weight_percent": largest_weight * 100,
            "top_three_weight_percent": top_three_weight * 100,
            "industry_exposure_percent": {
                industry: weight * 100
                for industry, weight in sorted(
                    industry_weights.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
            },
            "annualized_volatility_percent": (
                annualized_volatility * 100
                if annualized_volatility is not None
                else None
            ),
            "maximum_drawdown_percent": (
                maximum_drawdown * 100 if maximum_drawdown is not None else None
            ),
            "missing_history_position_ratio_percent": missing_ratio * 100,
            "risk_flags": flags,
            "positions": per_position,
            "methodology": {
                "volatility": "样本日收益协方差矩阵年化，年化系数252",
                "drawdown": "按当前持仓权重合成历史日收益后计算样本期最大回撤",
                "contribution": "当前权重乘以边际协方差，再除以组合方差",
            },
        },
        evidence=evidence,
        warnings=warnings,
    )
