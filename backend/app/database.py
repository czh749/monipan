from collections.abc import Generator
import os
from pathlib import Path

from sqlalchemy import BigInteger, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_DATABASE_URL = f"sqlite:///{(DATA_DIR / 'monipan.db').as_posix()}"
DATABASE_URL = os.getenv("MONIPAN_DATABASE_URL", DEFAULT_DATABASE_URL)


class Base(DeclarativeBase):
    pass


engine_options: dict = {
    "connect_args": (
        {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
    ),
}
if DATABASE_URL.startswith("mysql"):
    # Docker 中的数据库连接可能因容器重启或空闲超时失效；借出连接前先检查，
    # 并定期回收连接，避免把失效连接交给请求处理线程。
    engine_options.update(pool_pre_ping=True, pool_recycle=1800)

engine = create_engine(DATABASE_URL, **engine_options)


if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def migrate_database() -> None:
    """Apply the small additive migrations needed by existing databases.

    The project intentionally has no migration framework yet. New tables are
    handled by ``create_all``; this helper applies the small set of additive or
    widening changes required by existing persistent databases.
    """
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())

    additive_market_columns = {
        "stocks": {
            "quote_trade_date": "DATE NULL",
        },
        "stock_bars": {
            "prev_close": "NUMERIC(12, 2) NULL",
            "change_amount": "NUMERIC(12, 2) NULL",
            "change_percent": "NUMERIC(10, 4) NULL",
        },
    }
    for table_name, definitions in additive_market_columns.items():
        if table_name not in table_names:
            continue
        existing_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        with engine.begin() as connection:
            for column_name, definition in definitions.items():
                if column_name not in existing_columns:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} ADD COLUMN "
                            f"{column_name} {definition}"
                        )
                    )

    if "orders" in table_names:
        order_columns = {column["name"] for column in inspector.get_columns("orders")}
        if "limit_price" not in order_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE orders ADD COLUMN limit_price NUMERIC(12, 2)")
                )
        if "idempotency_key" not in order_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE orders ADD COLUMN "
                        "idempotency_key VARCHAR(64) NULL"
                    )
                )

        order_unique_names = {
            constraint["name"]
            for constraint in inspect(engine).get_unique_constraints("orders")
            if constraint.get("name")
        }
        order_index_names = {
            index["name"] for index in inspect(engine).get_indexes("orders")
        }
        if "uq_order_account_idempotency" not in (
            order_unique_names | order_index_names
        ):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE UNIQUE INDEX uq_order_account_idempotency "
                        "ON orders (account_id, idempotency_key)"
                    )
                )

    if "users" in table_names:
        user_columns = {column["name"] for column in inspector.get_columns("users")}
        additive_columns = {
            "password_hash": "VARCHAR(255) NULL",
            "status": "VARCHAR(16) NOT NULL DEFAULT 'ACTIVE'",
            "last_login_at": "DATETIME NULL",
        }
        with engine.begin() as connection:
            for column_name, definition in additive_columns.items():
                if column_name not in user_columns:
                    connection.execute(
                        text(
                            f"ALTER TABLE users ADD COLUMN {column_name} {definition}"
                        )
                    )

    if "official_document_contents" in table_names:
        document_columns = {
            column["name"]
            for column in inspector.get_columns("official_document_contents")
        }
        if "document_date" not in document_columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE official_document_contents "
                        "ADD COLUMN document_date DATE NULL"
                    )
                )
        document_indexes = {
            index["name"]
            for index in inspect(engine).get_indexes("official_document_contents")
        }
        if "ix_official_document_contents_document_date" not in document_indexes:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "CREATE INDEX ix_official_document_contents_document_date "
                        "ON official_document_contents (document_date)"
                    )
                )
        date_sources = (
            (
                "ANNOUNCEMENT",
                "company_announcements",
                "SELECT announcement_date FROM company_announcements "
                "WHERE company_announcements.id = "
                "official_document_contents.document_id",
            ),
            (
                "REGULATORY_LETTER",
                "regulatory_letters",
                "SELECT issued_date FROM regulatory_letters "
                "WHERE regulatory_letters.id = "
                "official_document_contents.document_id",
            ),
            (
                "REGULATORY_REPLY",
                "regulatory_letter_replies",
                "SELECT COALESCE(regulatory_letter_replies.reply_date, "
                "regulatory_letters.issued_date) "
                "FROM regulatory_letter_replies "
                "JOIN regulatory_letters ON regulatory_letters.id = "
                "regulatory_letter_replies.regulatory_letter_id "
                "WHERE regulatory_letter_replies.id = "
                "official_document_contents.document_id",
            ),
        )
        with engine.begin() as connection:
            for document_type, required_table, date_query in date_sources:
                if required_table not in table_names:
                    continue
                connection.execute(
                    text(
                        "UPDATE official_document_contents "
                        f"SET document_date = ({date_query}) "
                        "WHERE document_date IS NULL "
                        f"AND document_type = '{document_type}'"
                    )
                )

    if engine.dialect.name == "mysql":
        if "agent_recommendations" in table_names:
            recommendation_indexes = {
                index["name"]
                for index in inspector.get_indexes("agent_recommendations")
            }
            if {
                "ix_agent_recommendations_run_id",
                "uq_agent_recommendation_run",
            }.issubset(recommendation_indexes):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            "ALTER TABLE agent_recommendations "
                            "DROP INDEX ix_agent_recommendations_run_id"
                        )
                    )

        # SQLite accepts integers wider than 32 bits even when the declared type
        # is INTEGER. A-share historical volumes can exceed MySQL INT, so upgrade
        # existing MySQL tables created by earlier builds before importing data.
        for table_name in ("stocks", "stock_bars"):
            if table_name not in table_names:
                continue
            volume_column = next(
                column
                for column in inspector.get_columns(table_name)
                if column["name"] == "volume"
            )
            if not isinstance(volume_column["type"], BigInteger):
                with engine.begin() as connection:
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} "
                            "MODIFY COLUMN volume BIGINT NOT NULL"
                        )
                    )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
