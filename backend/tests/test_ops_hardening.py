"""Static guards for dependency-free deployment hardening files."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def read_project_file(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_nginx_security_headers_cover_inherited_header_locations() -> None:
    headers = read_project_file("frontend/security-headers.conf")
    nginx = read_project_file("frontend/nginx.conf")
    dockerfile = read_project_file("frontend/Dockerfile")
    runbook = read_project_file("docs/production-hardening.md")

    for expected in (
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Content-Security-Policy-Report-Only",
    ):
        assert expected in headers
    assert headers.count(" always;") == 5
    # Server scope plus the two locations that define their own add_header.
    assert nginx.count("include /etc/nginx/monipan-security-headers.conf;") == 3
    assert "server_tokens off;" in nginx
    assert nginx.count("proxy_set_header X-Request-ID $request_id;") == 7
    assert "location = /livez" in nginx
    assert "location = /readyz" in nginx
    # The Compose-only hostname does not resolve during docker build.
    assert "RUN nginx -t" not in dockerfile
    assert "frontend_image=$(docker compose images -q frontend)" in runbook
    assert "docker run --rm --add-host backend:127.0.0.1" in runbook


def test_compose_limits_logs_and_uses_separate_health_checks() -> None:
    compose = read_project_file("compose.yaml")

    assert 'max-size: "10m"' in compose
    assert 'max-file: "5"' in compose
    assert compose.count("logging: *default-logging") == 3
    assert "http://127.0.0.1:8000/readyz" in compose
    assert '"http://127.0.0.1/"' in compose
    assert "MONIPAN_ENVIRONMENT:" in compose
    assert "MONIPAN_DISABLE_MARKET_LOOP:" in compose


def test_backup_script_has_atomicity_and_cleanup_guards() -> None:
    script = read_project_file("scripts/backup_mysql.sh")
    script_bytes = (PROJECT_ROOT / "scripts/backup_mysql.sh").read_bytes()
    attributes = read_project_file(".gitattributes")

    for expected in (
        "set -eu",
        "umask 077",
        "mktemp",
        "trap cleanup",
        "mysqldump",
        "--single-transaction",
        "--quick",
        "gzip -t",
        "sha256sum",
        "sha256sum -c",
        'mv -- "$temporary_file" "$backup_file"',
        'find "$backup_dir" -maxdepth 1',
        'mkdir "$lock_dir"',
        'if [ "$publish_complete" -eq 0 ]',
        "pwd -P",
    ):
        assert expected in script
    assert script.index("checksum_output=$(sha256sum") < script.index(
        'mv -- "$temporary_file" "$backup_file"'
    )
    assert "rm -rf" not in script
    assert "monipan_local_only" not in script
    assert "root_local_only" not in script
    assert b"\r\n" not in script_bytes
    assert "scripts/*.sh text eol=lf" in attributes
