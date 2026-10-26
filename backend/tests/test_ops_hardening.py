"""Deployment security guards and real Compose configuration merge checks."""

from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest


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
        "Content-Security-Policy",
    ):
        assert expected in headers
    assert headers.count(" always;") == 5
    assert "Content-Security-Policy-Report-Only" not in headers
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
    assert "COPY proxy-trust.conf /etc/nginx/monipan-proxy-trust.conf" in dockerfile


def test_proxy_trust_is_scoped_to_the_observed_production_edge() -> None:
    nginx = read_project_file("frontend/nginx.conf")
    local_trust = read_project_file("frontend/proxy-trust.conf")
    production_trust = read_project_file("deploy/nginx/proxy-trust.production.conf")
    hsts = read_project_file("deploy/nginx/host-hsts.conf")

    assert "set_real_ip_from" not in local_trust
    assert "default 0;" in local_trust
    assert "geo $realip_remote_addr $monipan_trusted_edge" in production_trust
    assert "172.18.0.1/32 1;" in production_trust
    assert "set_real_ip_from 172.18.0.1;" in production_trust
    assert "real_ip_header X-Real-IP;" in production_trust
    assert "real_ip_recursive off;" in production_trust
    assert '"1:https" https;' in nginx and '"1:http" http;' in nginx
    assert "default $scheme;" in nginx
    assert nginx.count("proxy_set_header X-Forwarded-For $remote_addr;") == 4
    assert nginx.count("proxy_set_header X-Forwarded-Proto $monipan_original_scheme;") == 4
    assert "$proxy_add_x_forwarded_for" not in nginx
    assert '"max-age=31536000" always;' in hsts
    assert "Strict-Transport-Security" not in read_project_file("frontend/security-headers.conf")


def compose_config(tmp_path: Path, production: bool, trusted_network: bool = True,
                   auto_select: bool = False):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI is needed; a running engine is not required")
    env_file = tmp_path / "fixture.env"
    env_file.write_text(
        "MONIPAN_MYSQL_PASSWORD=fixture-database-password\n"
        "MONIPAN_MYSQL_ROOT_PASSWORD=fixture-root-password\n"
        + ("MONIPAN_FORWARDED_ALLOW_IPS=172.18.0.0/16\n" if trusted_network else "")
        + ("COMPOSE_FILE=compose.yaml:compose.production.yaml\nCOMPOSE_PATH_SEPARATOR=:\n"
           if auto_select else ""),
        encoding="utf-8",
    )
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("MONIPAN_", "COMPOSE_"))
    }
    command = [docker, "compose", "--project-directory", str(PROJECT_ROOT),
               "--env-file", str(env_file), "-p", "monipan"]
    if not auto_select:
        command += ["-f", str(PROJECT_ROOT / "compose.yaml")]
    if production and not auto_select:
        command += ["-f", str(PROJECT_ROOT / "compose.production.yaml")]
    return subprocess.run(command + ["config", "--format", "json"],
                          cwd=PROJECT_ROOT, env=environment, capture_output=True,
                          text=True, encoding="utf-8", timeout=30)


def test_production_compose_merge_preserves_database_and_applies_trust(tmp_path) -> None:
    local_result = compose_config(tmp_path, production=False)
    production_result = compose_config(tmp_path, production=True)
    assert local_result.returncode == 0, local_result.stderr
    assert production_result.returncode == 0, production_result.stderr
    local = json.loads(local_result.stdout)
    production = json.loads(production_result.stdout)
    assert production["name"] == local["name"] == "monipan"
    assert production["services"]["mysql"] == local["services"]["mysql"]
    assert production["volumes"] == local["volumes"]
    assert production["networks"] == local["networks"]
    assert production["services"]["frontend"]["ports"] == local["services"]["frontend"]["ports"]
    environment = production["services"]["backend"]["environment"]
    assert environment["MONIPAN_ENVIRONMENT"] == "production"
    assert environment["MONIPAN_COOKIE_SECURE"] == "1"
    assert environment["FORWARDED_ALLOW_IPS"] == "172.18.0.0/16"
    assert environment["MONIPAN_DATABASE_URL"] == local["services"]["backend"]["environment"]["MONIPAN_DATABASE_URL"]
    assert local["services"]["backend"]["environment"]["MONIPAN_ENVIRONMENT"] == "development"
    assert local["services"]["backend"]["environment"]["MONIPAN_COOKIE_SECURE"] == "0"
    for config, expected_source in ((local, "frontend/proxy-trust.conf"),
                                    (production, "deploy/nginx/proxy-trust.production.conf")):
        mounts = config["services"]["frontend"]["volumes"]
        assert len(mounts) == 3
        assert all(mount["read_only"] for mount in mounts)
        trust = [mount for mount in mounts if mount["target"] == "/etc/nginx/monipan-proxy-trust.conf"]
        assert len(trust) == 1
        assert Path(trust[0]["source"]).resolve() == (PROJECT_ROOT / expected_source).resolve()


def test_production_compose_requires_an_explicit_trusted_network(tmp_path) -> None:
    result = compose_config(tmp_path, production=True, trusted_network=False)
    assert result.returncode != 0
    assert "Set the verified backend proxy network" in result.stderr


def test_production_env_selects_the_override_for_ordinary_compose_commands(tmp_path) -> None:
    result = compose_config(tmp_path, production=True, auto_select=True)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["services"]["backend"]["environment"]["MONIPAN_ENVIRONMENT"] == "production"
    trust = [mount for mount in config["services"]["frontend"]["volumes"]
             if mount["target"] == "/etc/nginx/monipan-proxy-trust.conf"]
    assert len(trust) == 1
    assert Path(trust[0]["source"]).name == "proxy-trust.production.conf"


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
