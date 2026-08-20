"""Runtime deployment checks that do not require third-party settings packages."""

from collections.abc import Mapping
from dataclasses import dataclass
import os


ALLOWED_ENVIRONMENTS = frozenset({"development", "test", "production"})
UNSAFE_DATABASE_MARKERS = (
    "monipan_local_only",
    "root_local_only",
    "replace-with-",
)


@dataclass(frozen=True)
class RuntimeConfiguration:
    environment: str
    production: bool


def validate_runtime_configuration(
    environ: Mapping[str, str] | None = None,
) -> RuntimeConfiguration:
    """Reject unsafe production defaults while keeping local development compatible.

    Validation intentionally runs from the application lifespan instead of module
    import time. Tests and command-line utilities can therefore set their isolated
    environment before the application starts.
    """

    values = os.environ if environ is None else environ
    environment = values.get("MONIPAN_ENVIRONMENT", "development").strip().lower()
    if environment not in ALLOWED_ENVIRONMENTS:
        allowed = ", ".join(sorted(ALLOWED_ENVIRONMENTS))
        raise RuntimeError(
            f"MONIPAN_ENVIRONMENT must be one of: {allowed}"
        )

    if environment != "production":
        return RuntimeConfiguration(environment=environment, production=False)

    errors: list[str] = []
    database_url = values.get("MONIPAN_DATABASE_URL", "").strip()
    if not database_url:
        errors.append("MONIPAN_DATABASE_URL is required")
    elif database_url.lower().startswith("sqlite"):
        errors.append("SQLite is not allowed in production")
    elif any(marker in database_url.lower() for marker in UNSAFE_DATABASE_MARKERS):
        errors.append(
            "MONIPAN_DATABASE_URL contains a development or placeholder password"
        )

    # auth.py intentionally uses the same exact "1" contract when it creates
    # the Cookie. Accepting aliases here would let validation pass while the
    # actual session Cookie remains non-Secure.
    if values.get("MONIPAN_COOKIE_SECURE", "") != "1":
        errors.append("MONIPAN_COOKIE_SECURE must equal 1")

    if errors:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))

    return RuntimeConfiguration(environment=environment, production=True)
