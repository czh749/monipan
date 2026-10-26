"""Frozen v0.4 schema at 2026-10-18. Never regenerate after this revision ships.

Definitions were rendered from the ORM once; this module imports no live models.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql


class _Definitions:
    def __init__(self, tables):
        self.tables = tables

    def create_table(self, name, *columns, **options):
        options.update(mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_0900_ai_ci")
        return sa.Table(name, self.tables, *columns, **options)

    def create_index(self, name, table, columns, unique=False, **options):
        return sa.Index(name, *[self.tables.tables[table].c[column] for column in columns], unique=unique, **options)

    @staticmethod
    def f(name):
        return name


def metadata():
    tables = sa.MetaData()
    op = _Definitions(tables)
    # Frozen definitions rendered once for this baseline.
    op.create_table('market_indices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('exchange', sa.String(length=8), nullable=False),
    sa.Column('display_order', sa.Integer(), nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('change', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('change_percent', sa.Numeric(precision=8, scale=2), nullable=False),
    sa.Column('turnover', sa.Numeric(precision=22, scale=2), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_market_indices_symbol'), 'market_indices', ['symbol'], unique=True)
    op.create_table('rate_limit_counters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scope', sa.String(length=64), nullable=False),
    sa.Column('identity_hash', sa.String(length=64), nullable=False),
    sa.Column('window_start', sa.DateTime(), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('scope', 'identity_hash', 'window_start', name='uq_rate_limit_scope_identity_window')
    )
    op.create_index('ix_rate_limit_counter_expires', 'rate_limit_counters', ['expires_at'], unique=False)
    op.create_table('rate_limit_leases',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scope', sa.String(length=64), nullable=False),
    sa.Column('identity_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('scope', 'identity_hash', name='uq_rate_limit_lease_scope_identity')
    )
    op.create_index('ix_rate_limit_lease_expires', 'rate_limit_leases', ['expires_at'], unique=False)
    op.create_table('stocks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('exchange', sa.String(length=8), nullable=False),
    sa.Column('industry', sa.String(length=40), nullable=False),
    sa.Column('prev_close', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('open_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('high_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('low_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('volume', sa.BigInteger(), nullable=False),
    sa.Column('quote_trade_date', sa.Date(), nullable=True),
    sa.Column('quote_source_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_stocks_name'), 'stocks', ['name'], unique=False)
    op.create_index(op.f('ix_stocks_symbol'), 'stocks', ['symbol'], unique=True)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=50), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('last_login_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_username'), 'users', ['username'], unique=True)
    op.create_table('announcement_sync_states',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('exchange', sa.String(length=8), nullable=False),
    sa.Column('covered_from', sa.Date(), nullable=False),
    sa.Column('covered_to', sa.Date(), nullable=False),
    sa.Column('last_success_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_id', 'exchange', name='uq_announcement_sync_stock_exchange')
    )
    op.create_index(op.f('ix_announcement_sync_states_stock_id'), 'announcement_sync_states', ['stock_id'], unique=False)
    op.create_table('auth_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_auth_sessions_expires_at'), 'auth_sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_auth_sessions_token_hash'), 'auth_sessions', ['token_hash'], unique=True)
    op.create_index(op.f('ix_auth_sessions_user_id'), 'auth_sessions', ['user_id'], unique=False)
    op.create_table('company_announcements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('exchange', sa.String(length=8), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('announcement_date', sa.Date(), nullable=False),
    sa.Column('announcement_heading', sa.String(length=64), nullable=True),
    sa.Column('announcement_type', sa.String(length=128), nullable=True),
    sa.Column('source_url', sa.String(length=800), nullable=False),
    sa.Column('source_published_at', sa.DateTime(), nullable=True),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_id', 'exchange', 'external_id', name='uq_company_announcement_stock_exchange_external_id')
    )
    op.create_index('ix_company_announcement_stock_date', 'company_announcements', ['stock_id', 'announcement_date'], unique=False)
    op.create_index(op.f('ix_company_announcements_announcement_date'), 'company_announcements', ['announcement_date'], unique=False)
    op.create_index(op.f('ix_company_announcements_exchange'), 'company_announcements', ['exchange'], unique=False)
    op.create_index(op.f('ix_company_announcements_fetched_at'), 'company_announcements', ['fetched_at'], unique=False)
    op.create_index(op.f('ix_company_announcements_stock_id'), 'company_announcements', ['stock_id'], unique=False)
    op.create_table('financial_reports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('report_period', sa.Date(), nullable=False),
    sa.Column('report_type', sa.String(length=16), nullable=False),
    sa.Column('report_name', sa.String(length=40), nullable=False),
    sa.Column('announcement_date', sa.Date(), nullable=False),
    sa.Column('statement_scope', sa.String(length=16), nullable=False),
    sa.Column('currency', sa.String(length=8), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('revenue', sa.Numeric(precision=22, scale=2), nullable=True),
    sa.Column('net_profit_parent', sa.Numeric(precision=22, scale=2), nullable=True),
    sa.Column('basic_eps', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('deducted_eps', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('weighted_roe', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('gross_margin', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('revenue_yoy', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('net_profit_yoy', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('book_value_per_share', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('operating_cash_flow_per_share', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('source_url', sa.String(length=500), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('source_updated_at', sa.DateTime(), nullable=True),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_id', 'report_period', 'statement_scope', 'version', name='uq_financial_report_version')
    )
    op.create_index(op.f('ix_financial_reports_announcement_date'), 'financial_reports', ['announcement_date'], unique=False)
    op.create_index(op.f('ix_financial_reports_fetched_at'), 'financial_reports', ['fetched_at'], unique=False)
    op.create_index(op.f('ix_financial_reports_report_period'), 'financial_reports', ['report_period'], unique=False)
    op.create_index(op.f('ix_financial_reports_status'), 'financial_reports', ['status'], unique=False)
    op.create_index(op.f('ix_financial_reports_stock_id'), 'financial_reports', ['stock_id'], unique=False)
    op.create_table('invitation_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=100), nullable=True),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.Column('disabled_at', sa.DateTime(), nullable=True),
    sa.Column('used_by_user_id', sa.Integer(), nullable=True),
    sa.Column('used_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['used_by_user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('used_by_user_id')
    )
    op.create_index(op.f('ix_invitation_codes_code_hash'), 'invitation_codes', ['code_hash'], unique=True)
    op.create_index(op.f('ix_invitation_codes_expires_at'), 'invitation_codes', ['expires_at'], unique=False)
    op.create_table('official_document_contents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('document_type', sa.String(length=32), nullable=False),
    sa.Column('document_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('document_date', sa.Date(), nullable=True),
    sa.Column('source_url', sa.String(length=800), nullable=False),
    sa.Column('source_content_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('mime_type', sa.String(length=128), nullable=False),
    sa.Column('file_size', sa.BigInteger(), nullable=False),
    sa.Column('file_sha256', sa.String(length=64), nullable=False),
    sa.Column('page_count', sa.Integer(), nullable=False),
    sa.Column('text_content', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('page_offsets', sa.JSON(), nullable=False),
    sa.Column('text_sha256', sa.String(length=64), nullable=False),
    sa.Column('extraction_warning', sa.String(length=1000), nullable=True),
    sa.Column('downloaded_at', sa.DateTime(), nullable=False),
    sa.Column('extracted_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('document_type', 'document_id', name='uq_official_document_type_id')
    )
    op.create_index(op.f('ix_official_document_contents_document_date'), 'official_document_contents', ['document_date'], unique=False)
    op.create_index(op.f('ix_official_document_contents_document_id'), 'official_document_contents', ['document_id'], unique=False)
    op.create_index(op.f('ix_official_document_contents_file_sha256'), 'official_document_contents', ['file_sha256'], unique=False)
    op.create_index(op.f('ix_official_document_contents_status'), 'official_document_contents', ['status'], unique=False)
    op.create_index(op.f('ix_official_document_contents_stock_id'), 'official_document_contents', ['stock_id'], unique=False)
    op.create_index('ix_official_document_stock_type', 'official_document_contents', ['stock_id', 'document_type'], unique=False)
    op.create_table('performance_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=16), nullable=False),
    sa.Column('report_period', sa.Date(), nullable=False),
    sa.Column('report_name', sa.String(length=40), nullable=False),
    sa.Column('announcement_date', sa.Date(), nullable=False),
    sa.Column('forecast_type', sa.String(length=32), nullable=True),
    sa.Column('revenue_lower', sa.Numeric(precision=22, scale=2), nullable=True),
    sa.Column('revenue_upper', sa.Numeric(precision=22, scale=2), nullable=True),
    sa.Column('revenue_growth_lower', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('revenue_growth_upper', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('net_profit_lower', sa.Numeric(precision=22, scale=2), nullable=True),
    sa.Column('net_profit_upper', sa.Numeric(precision=22, scale=2), nullable=True),
    sa.Column('net_profit_growth_lower', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('net_profit_growth_upper', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('summary', sa.String(length=1200), nullable=True),
    sa.Column('reason', sa.String(length=2000), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('source_url', sa.String(length=500), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_id', 'event_type', 'report_period', 'announcement_date', name='uq_performance_event_period_date')
    )
    op.create_index(op.f('ix_performance_events_announcement_date'), 'performance_events', ['announcement_date'], unique=False)
    op.create_index(op.f('ix_performance_events_fetched_at'), 'performance_events', ['fetched_at'], unique=False)
    op.create_index(op.f('ix_performance_events_report_period'), 'performance_events', ['report_period'], unique=False)
    op.create_index(op.f('ix_performance_events_stock_id'), 'performance_events', ['stock_id'], unique=False)
    op.create_table('raw_close_syncs',
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('last_attempt_at', sa.DateTime(), nullable=False),
    sa.Column('last_success_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('stock_id')
    )
    op.create_table('regulatory_letters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('exchange', sa.String(length=8), nullable=False),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('letter_type', sa.String(length=128), nullable=False),
    sa.Column('issued_date', sa.Date(), nullable=False),
    sa.Column('source_url', sa.String(length=800), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_id', 'exchange', 'external_id', name='uq_regulatory_letter_stock_exchange_external_id')
    )
    op.create_index('ix_regulatory_letter_stock_date', 'regulatory_letters', ['stock_id', 'issued_date'], unique=False)
    op.create_index(op.f('ix_regulatory_letters_exchange'), 'regulatory_letters', ['exchange'], unique=False)
    op.create_index(op.f('ix_regulatory_letters_fetched_at'), 'regulatory_letters', ['fetched_at'], unique=False)
    op.create_index(op.f('ix_regulatory_letters_issued_date'), 'regulatory_letters', ['issued_date'], unique=False)
    op.create_index(op.f('ix_regulatory_letters_stock_id'), 'regulatory_letters', ['stock_id'], unique=False)
    op.create_table('regulatory_sync_states',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('exchange', sa.String(length=8), nullable=False),
    sa.Column('covered_from', sa.Date(), nullable=False),
    sa.Column('covered_to', sa.Date(), nullable=False),
    sa.Column('last_success_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_id', 'exchange', name='uq_regulatory_sync_stock_exchange')
    )
    op.create_index(op.f('ix_regulatory_sync_states_stock_id'), 'regulatory_sync_states', ['stock_id'], unique=False)
    op.create_table('simulation_accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('initial_cash', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('available_cash', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('available_cash >= 0', name='ck_account_cash_nonnegative'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_simulation_accounts_user_id'), 'simulation_accounts', ['user_id'], unique=True)
    op.create_table('stock_bars',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('open_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('high_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('low_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('close_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('prev_close', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('change_amount', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('change_percent', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('volume', sa.BigInteger(), nullable=False),
    sa.Column('turnover', sa.Numeric(precision=22, scale=2), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_id', 'trade_date', name='uq_stock_bar_date')
    )
    op.create_index(op.f('ix_stock_bars_stock_id'), 'stock_bars', ['stock_id'], unique=False)
    op.create_index(op.f('ix_stock_bars_trade_date'), 'stock_bars', ['trade_date'], unique=False)
    op.create_table('stock_raw_closes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('close_price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('stock_id', 'trade_date', name='uq_stock_raw_close_date')
    )
    op.create_index(op.f('ix_stock_raw_closes_stock_id'), 'stock_raw_closes', ['stock_id'], unique=False)
    op.create_index(op.f('ix_stock_raw_closes_trade_date'), 'stock_raw_closes', ['trade_date'], unique=False)
    op.create_table('account_daily_snapshots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('snapshot_date', sa.Date(), nullable=False),
    sa.Column('cash', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('market_value', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('total_assets', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('cost_basis', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('realized_pnl', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('floating_pnl', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('cash_change', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('market_value_change', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('asset_change', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('asset_change_delta', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('trade_count', sa.Integer(), nullable=False),
    sa.Column('trade_cash_flow', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('fees', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('ledger_cash_delta', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('ledger_consistent', sa.Boolean(), nullable=False),
    sa.Column('reconciliation_delta', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('valuation_status', sa.String(length=16), nullable=False),
    sa.Column('missing_symbols', sa.JSON(), nullable=False),
    sa.Column('positions', sa.JSON(), nullable=False),
    sa.Column('calculated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['simulation_accounts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'snapshot_date', name='uq_account_snapshot_date')
    )
    op.create_index(op.f('ix_account_daily_snapshots_account_id'), 'account_daily_snapshots', ['account_id'], unique=False)
    op.create_index(op.f('ix_account_daily_snapshots_snapshot_date'), 'account_daily_snapshots', ['snapshot_date'], unique=False)
    op.create_table('agent_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=True),
    sa.Column('stock_id', sa.Integer(), nullable=True),
    sa.Column('run_type', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('model_provider', sa.String(length=32), nullable=False),
    sa.Column('model_name', sa.String(length=100), nullable=False),
    sa.Column('request_text', sa.Text(), nullable=True),
    sa.Column('data_as_of', sa.DateTime(), nullable=True),
    sa.Column('input_tokens', sa.BigInteger(), nullable=False),
    sa.Column('output_tokens', sa.BigInteger(), nullable=False),
    sa.Column('total_tokens', sa.BigInteger(), nullable=False),
    sa.Column('error_code', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.String(length=2000), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("run_type <> 'PORTFOLIO_ANALYSIS' OR account_id IS NOT NULL", name='ck_agent_run_portfolio_scope'),
    sa.CheckConstraint("run_type <> 'STOCK_ANALYSIS' OR stock_id IS NOT NULL", name='ck_agent_run_stock_scope'),
    sa.CheckConstraint("run_type IN ('STOCK_ANALYSIS', 'PORTFOLIO_ANALYSIS')", name='ck_agent_run_type'),
    sa.CheckConstraint("status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')", name='ck_agent_run_status'),
    sa.CheckConstraint('input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0', name='ck_agent_run_tokens_nonnegative'),
    sa.ForeignKeyConstraint(['account_id'], ['simulation_accounts.id'], ),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_agent_run_stock_created', 'agent_runs', ['stock_id', 'created_at'], unique=False)
    op.create_index('ix_agent_run_user_created', 'agent_runs', ['user_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_agent_runs_account_id'), 'agent_runs', ['account_id'], unique=False)
    op.create_index(op.f('ix_agent_runs_status'), 'agent_runs', ['status'], unique=False)
    op.create_index(op.f('ix_agent_runs_stock_id'), 'agent_runs', ['stock_id'], unique=False)
    op.create_index(op.f('ix_agent_runs_user_id'), 'agent_runs', ['user_id'], unique=False)
    op.create_table('daily_review_notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('review_date', sa.Date(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['simulation_accounts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'review_date', name='uq_account_review_date')
    )
    op.create_index(op.f('ix_daily_review_notes_account_id'), 'daily_review_notes', ['account_id'], unique=False)
    op.create_index(op.f('ix_daily_review_notes_review_date'), 'daily_review_notes', ['review_date'], unique=False)
    op.create_table('official_document_chunks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('official_document_content_id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('document_type', sa.String(length=32), nullable=False),
    sa.Column('page_number', sa.Integer(), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('start_char', sa.Integer(), nullable=False),
    sa.Column('end_char', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('text_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['official_document_content_id'], ['official_document_contents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('official_document_content_id', 'chunk_index', name='uq_official_document_chunk_content_index')
    )
    op.create_index('ix_official_document_chunk_stock_type', 'official_document_chunks', ['stock_id', 'document_type'], unique=False)
    op.create_index(op.f('ix_official_document_chunks_document_type'), 'official_document_chunks', ['document_type'], unique=False)
    op.create_index(op.f('ix_official_document_chunks_official_document_content_id'), 'official_document_chunks', ['official_document_content_id'], unique=False)
    op.create_index(op.f('ix_official_document_chunks_page_number'), 'official_document_chunks', ['page_number'], unique=False)
    op.create_index(op.f('ix_official_document_chunks_stock_id'), 'official_document_chunks', ['stock_id'], unique=False)
    op.create_index(op.f('ix_official_document_chunks_text_hash'), 'official_document_chunks', ['text_hash'], unique=False)
    op.create_table('orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_no', sa.String(length=32), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('order_type', sa.String(length=10), nullable=False),
    sa.Column('limit_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('submitted_quote_price', sa.Numeric(precision=12, scale=2), nullable=True),
    sa.Column('submitted_quote_at', sa.DateTime(), nullable=True),
    sa.Column('filled_quote_at', sa.DateTime(), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('filled_quantity', sa.Integer(), nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('fee', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('reject_reason', sa.String(length=200), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['simulation_accounts.id'], ),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'idempotency_key', name='uq_order_account_idempotency')
    )
    op.create_index(op.f('ix_orders_account_id'), 'orders', ['account_id'], unique=False)
    op.create_index(op.f('ix_orders_created_at'), 'orders', ['created_at'], unique=False)
    op.create_index(op.f('ix_orders_order_no'), 'orders', ['order_no'], unique=True)
    op.create_index(op.f('ix_orders_stock_id'), 'orders', ['stock_id'], unique=False)
    op.create_table('positions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('average_cost', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('quantity >= 0', name='ck_position_quantity_nonnegative'),
    sa.ForeignKeyConstraint(['account_id'], ['simulation_accounts.id'], ),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'stock_id', name='uq_position_account_stock')
    )
    op.create_index(op.f('ix_positions_account_id'), 'positions', ['account_id'], unique=False)
    op.create_index(op.f('ix_positions_stock_id'), 'positions', ['stock_id'], unique=False)
    op.create_table('regulatory_letter_replies',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('regulatory_letter_id', sa.Integer(), nullable=False),
    sa.Column('announcement_id', sa.Integer(), nullable=True),
    sa.Column('external_id', sa.String(length=128), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('reply_date', sa.Date(), nullable=True),
    sa.Column('source_url', sa.String(length=800), nullable=False),
    sa.Column('match_method', sa.String(length=32), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=False),
    sa.Column('fetched_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['announcement_id'], ['company_announcements.id'], ),
    sa.ForeignKeyConstraint(['regulatory_letter_id'], ['regulatory_letters.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('regulatory_letter_id', 'external_id', name='uq_regulatory_reply_letter_external_id')
    )
    op.create_index(op.f('ix_regulatory_letter_replies_announcement_id'), 'regulatory_letter_replies', ['announcement_id'], unique=False)
    op.create_index(op.f('ix_regulatory_letter_replies_fetched_at'), 'regulatory_letter_replies', ['fetched_at'], unique=False)
    op.create_index(op.f('ix_regulatory_letter_replies_regulatory_letter_id'), 'regulatory_letter_replies', ['regulatory_letter_id'], unique=False)
    op.create_index(op.f('ix_regulatory_letter_replies_reply_date'), 'regulatory_letter_replies', ['reply_date'], unique=False)
    op.create_table('watchlist_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['simulation_accounts.id'], ),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('account_id', 'stock_id', name='uq_watchlist_account_stock')
    )
    op.create_index(op.f('ix_watchlist_items_account_id'), 'watchlist_items', ['account_id'], unique=False)
    op.create_index(op.f('ix_watchlist_items_stock_id'), 'watchlist_items', ['stock_id'], unique=False)
    op.create_table('account_transactions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('transaction_type', sa.String(length=20), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('balance_after', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['simulation_accounts.id'], ),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_account_transactions_account_id'), 'account_transactions', ['account_id'], unique=False)
    op.create_index(op.f('ix_account_transactions_created_at'), 'account_transactions', ['created_at'], unique=False)
    op.create_table('agent_evidence',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('evidence_key', sa.String(length=191), nullable=False),
    sa.Column('tool_name', sa.String(length=64), nullable=False),
    sa.Column('evidence_type', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=True),
    sa.Column('source_url', sa.String(length=1200), nullable=True),
    sa.Column('source_date', sa.Date(), nullable=True),
    sa.Column('source_published_at', sa.DateTime(), nullable=True),
    sa.Column('page_number', sa.Integer(), nullable=True),
    sa.Column('start_char', sa.Integer(), nullable=True),
    sa.Column('end_char', sa.Integer(), nullable=True),
    sa.Column('excerpt', sa.Text(), nullable=False),
    sa.Column('content_hash', sa.String(length=64), nullable=True),
    sa.Column('trust_level', sa.String(length=32), nullable=False),
    sa.Column('metadata', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint('end_char IS NULL OR end_char >= 0', name='ck_agent_evidence_end_nonnegative'),
    sa.CheckConstraint('page_number IS NULL OR page_number > 0', name='ck_agent_evidence_page_positive'),
    sa.CheckConstraint('start_char IS NULL OR end_char IS NULL OR end_char >= start_char', name='ck_agent_evidence_range'),
    sa.CheckConstraint('start_char IS NULL OR start_char >= 0', name='ck_agent_evidence_start_nonnegative'),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'evidence_key', name='uq_agent_evidence_run_key')
    )
    op.create_index(op.f('ix_agent_evidence_run_id'), 'agent_evidence', ['run_id'], unique=False)
    op.create_index('ix_agent_evidence_run_tool', 'agent_evidence', ['run_id', 'tool_name'], unique=False)
    op.create_index(op.f('ix_agent_evidence_source_date'), 'agent_evidence', ['source_date'], unique=False)
    op.create_table('agent_recommendations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=False),
    sa.Column('risk_level', sa.String(length=16), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('time_horizon', sa.String(length=32), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('reasoning', sa.Text().with_variant(mysql.LONGTEXT(), 'mysql'), nullable=False),
    sa.Column('positive_factors', sa.JSON(), nullable=False),
    sa.Column('risk_factors', sa.JSON(), nullable=False),
    sa.Column('action_conditions', sa.JSON(), nullable=False),
    sa.Column('invalidation_conditions', sa.JSON(), nullable=False),
    sa.Column('evidence_keys', sa.JSON(), nullable=False),
    sa.Column('disclaimer', sa.String(length=500), nullable=False),
    sa.Column('output_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("action IN ('WATCH', 'HOLD', 'AVOID', 'REDUCE', 'CONSIDER_ADD', 'NO_CONCLUSION')", name='ck_agent_recommendation_action'),
    sa.CheckConstraint("risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'UNKNOWN')", name='ck_agent_recommendation_risk_level'),
    sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name='ck_agent_recommendation_confidence'),
    sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', name='uq_agent_recommendation_run')
    )
    op.create_table('trades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('trade_no', sa.String(length=32), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('stock_id', sa.Integer(), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('fee', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('filled_quote_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['simulation_accounts.id'], ),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ),
    sa.ForeignKeyConstraint(['stock_id'], ['stocks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_trades_account_id'), 'trades', ['account_id'], unique=False)
    op.create_index(op.f('ix_trades_created_at'), 'trades', ['created_at'], unique=False)
    op.create_index(op.f('ix_trades_order_id'), 'trades', ['order_id'], unique=False)
    op.create_index(op.f('ix_trades_stock_id'), 'trades', ['stock_id'], unique=False)
    op.create_index(op.f('ix_trades_trade_no'), 'trades', ['trade_no'], unique=True)
    op.create_table('trade_notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('trade_id', sa.Integer(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['simulation_accounts.id'], ),
    sa.ForeignKeyConstraint(['trade_id'], ['trades.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('trade_id', name='uq_trade_note_trade')
    )
    op.create_index(op.f('ix_trade_notes_account_id'), 'trade_notes', ['account_id'], unique=False)
    op.create_index(op.f('ix_trade_notes_trade_id'), 'trade_notes', ['trade_id'], unique=False)
    # ### end Alembic commands ###
    return tables
