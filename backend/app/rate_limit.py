"""Database-backed request quotas and short-lived concurrency leases."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from ipaddress import ip_address
from math import ceil

from fastapi import HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .database import SessionLocal
from .models import RateLimitCounter, RateLimitLease


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    count: int
    limit: int
    retry_after: int


@dataclass(frozen=True)
class AnalysisLease:
    user_identity: str
    global_slot: str


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def identity_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def client_ip(request: Request) -> str:
    """Read the address overwritten by our Nginx proxy, never an arbitrary chain."""
    candidate = request.headers.get("x-real-ip", "").strip()
    if candidate:
        try:
            return str(ip_address(candidate))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch = int(now.replace(tzinfo=UTC).timestamp())
    start_epoch = epoch - epoch % window_seconds
    return datetime.fromtimestamp(start_epoch, UTC).replace(tzinfo=None)


def consume_fixed_window(
    *,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    """Atomically consume one request from a shared fixed-window quota."""
    if limit < 1 or window_seconds < 1:
        raise ValueError("Rate-limit values must be positive")

    now = utcnow()
    window_start = _window_start(now, window_seconds)
    expires_at = window_start + timedelta(seconds=window_seconds)
    hashed = identity_hash(identity)

    for _attempt in range(3):
        try:
            with SessionLocal() as db:
                counter = db.scalar(
                    select(RateLimitCounter)
                    .where(
                        RateLimitCounter.scope == scope,
                        RateLimitCounter.identity_hash == hashed,
                        RateLimitCounter.window_start == window_start,
                    )
                    .with_for_update()
                )
                if counter is None:
                    counter = RateLimitCounter(
                        scope=scope,
                        identity_hash=hashed,
                        window_start=window_start,
                        count=1,
                        expires_at=expires_at,
                    )
                    db.add(counter)
                else:
                    counter.count += 1
                    counter.updated_at = now

                db.flush()
                count = counter.count
                if count == 1:
                    db.execute(
                        delete(RateLimitCounter).where(
                            RateLimitCounter.expires_at < now - timedelta(days=1)
                        )
                    )
                db.commit()
                retry_after = max(1, ceil((expires_at - now).total_seconds()))
                return RateLimitDecision(
                    allowed=count <= limit,
                    count=count,
                    limit=limit,
                    retry_after=retry_after,
                )
        except IntegrityError:
            # Two first requests can race to create a bucket; retry and lock it.
            continue
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "RATE_LIMIT_UNAVAILABLE",
                    "message": "访问保护服务暂时不可用，请稍后重试",
                },
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "RATE_LIMIT_UNAVAILABLE",
            "message": "访问保护服务暂时繁忙，请稍后重试",
        },
    )


def enforce_fixed_window(
    *,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int,
    message: str,
) -> RateLimitDecision:
    decision = consume_fixed_window(
        scope=scope,
        identity=identity,
        limit=limit,
        window_seconds=window_seconds,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMITED",
                "message": message,
                "retry_after": decision.retry_after,
            },
            headers={"Retry-After": str(decision.retry_after)},
        )
    return decision


def clear_fixed_window(*, scope: str, identity: str) -> None:
    """Clear a failure bucket after successful authentication."""
    try:
        with SessionLocal() as db:
            db.execute(
                delete(RateLimitCounter).where(
                    RateLimitCounter.scope == scope,
                    RateLimitCounter.identity_hash == identity_hash(identity),
                )
            )
            db.commit()
    except SQLAlchemyError:
        # A successful login should not fail merely because cleanup failed.
        return


def _acquire_lease(*, scope: str, identity: str, ttl_seconds: int) -> bool:
    now = utcnow()
    hashed = identity_hash(identity)
    try:
        with SessionLocal() as db:
            db.execute(
                delete(RateLimitLease).where(
                    RateLimitLease.scope == scope,
                    RateLimitLease.identity_hash == hashed,
                    RateLimitLease.expires_at <= now,
                )
            )
            db.add(
                RateLimitLease(
                    scope=scope,
                    identity_hash=hashed,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                )
            )
            db.commit()
            return True
    except IntegrityError:
        return False
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RATE_LIMIT_UNAVAILABLE",
                "message": "AI 并发保护服务暂时不可用，请稍后重试",
            },
        ) from exc


def _release_lease(*, scope: str, identity: str) -> None:
    try:
        with SessionLocal() as db:
            db.execute(
                delete(RateLimitLease).where(
                    RateLimitLease.scope == scope,
                    RateLimitLease.identity_hash == identity_hash(identity),
                )
            )
            db.commit()
    except SQLAlchemyError:
        # The lease has a TTL and will self-heal after a worker crash or DB error.
        return


def acquire_analysis_lease(
    *,
    user_id: int,
    global_limit: int,
    ttl_seconds: int,
) -> AnalysisLease:
    user_identity = str(user_id)
    if not _acquire_lease(
        scope="ai_user_concurrency",
        identity=user_identity,
        ttl_seconds=ttl_seconds,
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "AI_ALREADY_RUNNING",
                "message": "你已有一项 AI 分析正在运行，请等待完成后再试",
            },
            headers={"Retry-After": "30"},
        )

    for slot_number in range(global_limit):
        slot = f"slot-{slot_number}"
        if _acquire_lease(
            scope="ai_global_concurrency",
            identity=slot,
            ttl_seconds=ttl_seconds,
        ):
            return AnalysisLease(
                user_identity=user_identity,
                global_slot=slot,
            )

    _release_lease(scope="ai_user_concurrency", identity=user_identity)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "code": "AI_CAPACITY_BUSY",
            "message": "当前 AI 分析任务较多，请稍后再试",
        },
        headers={"Retry-After": "30"},
    )


def release_analysis_lease(lease: AnalysisLease) -> None:
    _release_lease(
        scope="ai_global_concurrency",
        identity=lease.global_slot,
    )
    _release_lease(
        scope="ai_user_concurrency",
        identity=lease.user_identity,
    )
