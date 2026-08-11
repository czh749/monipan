"""Website authentication backed by Argon2 passwords and opaque DB sessions."""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import os
from secrets import token_urlsafe

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from .database import get_db
from .models import AuthSession, SimulationAccount, User
from .schemas import RegisterIn


SESSION_COOKIE_NAME = os.getenv("MONIPAN_SESSION_COOKIE", "monipan_session")
SESSION_DAYS = max(1, int(os.getenv("MONIPAN_SESSION_DAYS", "7")))
COOKIE_SECURE = os.getenv("MONIPAN_COOKIE_SECURE", "0") == "1"
PASSWORD_HASHER = PasswordHasher()
# Verifying this hash when a username is absent keeps login timing less revealing.
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("monipan-dummy-password-2026")


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="登录状态已失效，请重新登录",
        headers={"WWW-Authenticate": "Session"},
    )


def hash_token(raw_token: str) -> str:
    return sha256(raw_token.encode("utf-8")).hexdigest()


def user_values(user: User) -> dict:
    return {
        "username": user.username,
        "initial_cash": user.account.initial_cash,
        "created_at": user.created_at,
    }


def register_user(db: Session, payload: RegisterIn) -> User:
    if payload.username == "demo":
        raise HTTPException(status_code=409, detail="该用户名不可注册")
    if db.scalar(select(User.id).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="用户名已被使用")

    user = User(
        username=payload.username,
        password_hash=PASSWORD_HASHER.hash(payload.password),
        status="ACTIVE",
    )
    try:
        db.add(user)
        db.flush()
        db.add(
            SimulationAccount(
                user_id=user.id,
                initial_cash=1_000_000,
                available_cash=1_000_000,
            )
        )
        db.commit()
        return db.scalar(
            select(User)
            .where(User.id == user.id)
            .options(joinedload(User.account))
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已被使用") from exc


def verify_credentials(db: Session, username: str, password: str) -> User:
    user = db.scalar(
        select(User)
        .where(User.username == username)
        .options(joinedload(User.account))
    )
    password_hash = user.password_hash if user and user.password_hash else DUMMY_PASSWORD_HASH
    try:
        PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHash):
        raise HTTPException(status_code=401, detail="用户名或密码错误") from None
    if user is None or user.password_hash is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if user.status != "ACTIVE":
        raise HTTPException(status_code=403, detail="账户已停用")
    if PASSWORD_HASHER.check_needs_rehash(user.password_hash):
        user.password_hash = PASSWORD_HASHER.hash(password)
    user.last_login_at = utcnow()
    return user


def issue_session(db: Session, user: User) -> str:
    now = utcnow()
    db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.expires_at <= now,
        )
    )
    raw_token = token_urlsafe(48)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(raw_token),
            expires_at=now + timedelta(days=SESSION_DAYS),
        )
    )
    db.commit()
    return raw_token


def set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def get_current_session(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthSession:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if not raw_token or len(raw_token) < 40:
        raise unauthorized()
    session = db.scalar(
        select(AuthSession)
        .where(
            AuthSession.token_hash == hash_token(raw_token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > utcnow(),
        )
        .options(joinedload(AuthSession.user).joinedload(User.account))
    )
    if session is None or session.user.status != "ACTIVE":
        raise unauthorized()
    return session


def get_current_user(
    session: AuthSession = Depends(get_current_session),
) -> User:
    return session.user


def get_current_account(
    user: User = Depends(get_current_user),
) -> SimulationAccount:
    return user.account


def revoke_session(db: Session, session: AuthSession) -> None:
    session.revoked_at = utcnow()
    db.commit()
