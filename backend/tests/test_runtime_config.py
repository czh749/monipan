"""Tests for dependency-free production runtime validation."""

import pytest

from app.runtime_config import validate_runtime_configuration


def test_development_configuration_keeps_local_defaults_compatible() -> None:
    result = validate_runtime_configuration({})

    assert result.environment == "development"
    assert result.production is False


def test_production_configuration_rejects_insecure_defaults() -> None:
    with pytest.raises(RuntimeError) as error:
        validate_runtime_configuration(
            {
                "MONIPAN_ENVIRONMENT": "production",
                "MONIPAN_DATABASE_URL": (
                    "mysql+pymysql://monipan:monipan_local_only@mysql/monipan"
                ),
                "MONIPAN_COOKIE_SECURE": "0",
            }
        )

    message = str(error.value)
    assert "MONIPAN_DATABASE_URL" in message
    assert "MONIPAN_COOKIE_SECURE" in message


@pytest.mark.parametrize(
    "cookie_secure",
    ["true", "yes", "on", "0", "", " 1 "],
)
def test_production_cookie_flag_matches_authentication_contract(
    cookie_secure: str,
) -> None:
    with pytest.raises(RuntimeError, match="must equal 1"):
        validate_runtime_configuration(
            {
                "MONIPAN_ENVIRONMENT": "production",
                "MONIPAN_DATABASE_URL": (
                    "mysql+pymysql://monipan:a-unique-secret@mysql/monipan"
                ),
                "MONIPAN_COOKIE_SECURE": cookie_secure,
            }
        )


@pytest.mark.parametrize(
    "database_url",
    [
        "",
        "sqlite:///production.db",
        "mysql+pymysql://monipan:replace-with-a-password@mysql/monipan",
    ],
)
def test_production_rejects_missing_sqlite_and_placeholder_database_urls(
    database_url: str,
) -> None:
    with pytest.raises(RuntimeError) as error:
        validate_runtime_configuration(
            {
                "MONIPAN_ENVIRONMENT": "production",
                "MONIPAN_DATABASE_URL": database_url,
                "MONIPAN_COOKIE_SECURE": "1",
            }
        )

    if database_url:
        assert database_url not in str(error.value)


def test_production_configuration_accepts_secure_mysql_settings() -> None:
    result = validate_runtime_configuration(
        {
            "MONIPAN_ENVIRONMENT": "production",
            "MONIPAN_DATABASE_URL": (
                "mysql+pymysql://monipan:a-unique-secret@mysql/monipan"
            ),
            "MONIPAN_COOKIE_SECURE": "1",
        }
    )

    assert result.environment == "production"
    assert result.production is True


def test_runtime_environment_rejects_unknown_values() -> None:
    with pytest.raises(RuntimeError, match="MONIPAN_ENVIRONMENT"):
        validate_runtime_configuration({"MONIPAN_ENVIRONMENT": "prod"})
