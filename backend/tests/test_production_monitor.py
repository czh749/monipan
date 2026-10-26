"""Synthetic host/email failures; no production services or real mail touched."""

import ast
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 25, 8, tzinfo=timezone.utc).timestamp()


@pytest.fixture(scope='session')
def generated_monitor_artifact(tmp_path_factory):
    # Build in a disposable tree, without touching the user's archived upload.
    project = tmp_path_factory.mktemp('monitor-build')
    for relative in ('scripts/monitor_production.py', 'deploy/systemd/monipan-monitor.service',
                     'deploy/systemd/monipan-monitor.timer'):
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    spec = importlib.util.spec_from_file_location('monitor_builder', ROOT / 'scripts/build_monitor.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.ROOT = project
    return builder.build()


@pytest.fixture(params=['source', 'artifact'])
def monitor(request, generated_monitor_artifact):
    path = ROOT / 'scripts/monitor_production.py' if request.param == 'source' else generated_monitor_artifact
    spec = importlib.util.spec_from_file_location('production_monitor', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def samples(monitor, bad=None):
    return {key: {'ok': key != bad, 'detail': 'synthetic'} for key in monitor.LABELS}


def step(monitor, state, bad, now, mails, sender=None):
    return monitor.advance(state, samples(monitor, bad), monitor.DEFAULT_POLICY, now,
                           sender or (lambda subject, body: mails.append((subject, body))))


def config(monitor):
    return {'version': 1, 'policy': monitor.DEFAULT_POLICY.copy(), 'smtp': {
        'host': 'smtp.example.test', 'port': 465, 'security': 'ssl',
        'username': 'sender@example.test', 'password': 'DO_NOT_LOG_AUTH_CODE',
        'sender': 'sender@example.test', 'recipient': 'receiver@example.test'}}


def test_threshold_dedup_daily_reminder_and_confirmed_recovery(monitor):
    state, mails = monitor.empty_state(), []
    step(monitor, state, 'readiness', NOW, mails)
    assert not mails
    step(monitor, state, 'readiness', NOW + 300, mails)
    assert len(mails) == 1 and '故障' in mails[0][1]
    step(monitor, state, 'readiness', NOW + 600, mails)
    step(monitor, state, None, NOW + 900, mails)
    assert len(mails) == 1  # First good sample is not a recovery.
    step(monitor, state, 'readiness', NOW + 86400, mails)
    assert len(mails) == 1
    step(monitor, state, 'readiness', NOW + 86700, mails)
    assert len(mails) == 2 and '持续故障' in mails[-1][1]
    step(monitor, state, None, NOW + 87000, mails)
    step(monitor, state, None, NOW + 87300, mails)
    assert len(mails) == 3 and '恢复' in mails[-1][0]
    step(monitor, state, None, NOW + 87600, mails)
    assert len(mails) == 3


def test_flapping_one_bad_sample_is_silent(monitor):
    state, mails = monitor.empty_state(), []
    for index in range(12):
        step(monitor, state, 'readiness' if index % 2 else None, NOW + index * 300, mails)
    assert not mails


def test_multiple_faults_are_one_email(monitor):
    state, mails = monitor.empty_state(), []
    results = {key: {'ok': False, 'detail': 'synthetic'} for key in monitor.LABELS}
    for offset in (0, 300):
        monitor.advance(state, results, monitor.DEFAULT_POLICY, NOW + offset,
                        lambda subject, body: mails.append(body))
    assert len(mails) == 1
    assert all(label in mails[0] for label in monitor.LABELS.values())


def test_retry_backoff_does_not_mark_sent_and_redacts_smtp_errors(monitor, capsys):
    state, mails, attempts = monitor.empty_state(), [], []

    def failing_sender(*args):
        attempts.append(args)
        raise TimeoutError('DO_NOT_LOG_AUTH_CODE receiver@example.test')

    step(monitor, state, 'readiness', NOW, mails, failing_sender)
    assert not step(monitor, state, 'readiness', NOW + 300, mails, failing_sender)
    item = state['checks']['readiness']
    assert not item['mailed'] and item['retry_at'] == NOW + 600
    assert not step(monitor, state, 'readiness', NOW + 600, mails, failing_sender)
    assert item['retry_at'] == NOW + 1200
    step(monitor, state, 'readiness', NOW + 900, mails, failing_sender)
    assert len(attempts) == 2
    step(monitor, state, 'readiness', NOW + 1200, mails)
    assert len(mails) == 1 and item['mailed']
    output = capsys.readouterr().out
    assert 'SMTP_SEND_FAILED' in output and 'DO_NOT_LOG' not in output and '@' not in output


def test_unsent_fault_recovered_has_no_false_recovery_email(monitor):
    state, mails = monitor.empty_state(), []
    failing = lambda *args: (_ for _ in ()).throw(OSError('network unavailable'))
    step(monitor, state, 'disk', NOW, mails, failing)
    step(monitor, state, 'disk', NOW + 300, mails, failing)
    step(monitor, state, None, NOW + 600, mails)
    step(monitor, state, None, NOW + 900, mails)
    assert not mails


def test_fault_returning_during_failed_recovery_retry_cancels_recovery(monitor):
    state, mails = monitor.empty_state(), []
    step(monitor, state, 'disk', NOW, mails)
    step(monitor, state, 'disk', NOW + 300, mails)
    step(monitor, state, None, NOW + 600, mails)
    failing = lambda *args: (_ for _ in ()).throw(OSError('network unavailable'))
    step(monitor, state, None, NOW + 900, mails, failing)
    step(monitor, state, 'disk', NOW + 1200, mails)
    step(monitor, state, 'disk', NOW + 1500, mails)
    assert len(mails) == 1  # No stale recovery while the check is bad again.


def test_mute_expires_and_remembers_already_sent_recovery(monitor):
    state, mails = monitor.empty_state(), []
    step(monitor, state, 'disk', NOW, mails)
    step(monitor, state, 'disk', NOW + 300, mails)
    state['mute_until'] = NOW + 1800
    step(monitor, state, None, NOW + 600, mails)
    step(monitor, state, None, NOW + 900, mails)
    assert len(mails) == 1
    step(monitor, state, None, NOW + 1800, mails)
    assert len(mails) == 2 and '恢复' in mails[-1][0]


def test_muted_new_fault_is_sent_only_if_still_bad_on_expiry(monitor):
    state, mails = monitor.empty_state(), []
    state['mute_until'] = NOW + 900
    for offset in (0, 300, 600):
        step(monitor, state, 'disk', NOW + offset, mails)
    assert not mails
    step(monitor, state, 'disk', NOW + 900, mails)
    assert len(mails) == 1


def test_probe_exceptions_are_independent_and_never_exposed(monitor, monkeypatch):
    monkeypatch.setattr(monitor, 'container_probe', lambda: (_ for _ in ()).throw(
        RuntimeError('PASSWORD=private')))
    monkeypatch.setattr(monitor, 'readiness_probe', lambda: (True, 'ready'))
    monkeypatch.setattr(monitor, 'disk_probe', lambda policy: (True, 'space_ok'))
    monkeypatch.setattr(monitor, 'backup_age_probe', lambda policy, now: (True, 'fresh'))
    monkeypatch.setattr(monitor, 'properties', lambda unit: {
        'LoadState': 'loaded', 'ActiveState': 'active' if unit.endswith('timer') else 'inactive',
        'UnitFileState': 'enabled', 'Result': 'success', 'ExecMainStatus': '0'})
    result = monitor.collect(monitor.DEFAULT_POLICY, NOW)
    assert not result['containers']['ok'] and result['readiness']['ok']
    assert result['backup_result']['ok']  # Inactive successful oneshot is normal.
    assert 'PASSWORD' not in json.dumps(result)


@pytest.mark.parametrize('active,result,status,expected', [
    ('activating', 'exit-code', '1', True), ('active', 'success', '0', True),
    ('failed', 'exit-code', '1', False), ('inactive', 'success', '0', True)])
def test_backup_running_is_not_previous_failure(monitor, monkeypatch, active, result, status, expected):
    monkeypatch.setattr(monitor, 'properties', lambda unit: {
        'LoadState': 'loaded', 'ActiveState': active, 'Result': result, 'ExecMainStatus': status})
    for key in ('container_probe', 'readiness_probe'):
        monkeypatch.setattr(monitor, key, lambda: (True, 'ok'))
    monkeypatch.setattr(monitor, 'disk_probe', lambda policy: (True, 'ok'))
    monkeypatch.setattr(monitor, 'backup_age_probe', lambda policy, now: (False, 'stale'))
    results = monitor.collect(monitor.DEFAULT_POLICY, NOW)
    assert results['backup_result']['ok'] is expected
    assert not results['backup_age']['ok']  # Running cannot hide a stale backup.


def test_backup_age_uses_completed_pair_and_filename_not_touched_mtime(monitor, tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, 'PROJECT', tmp_path)
    daily = tmp_path / 'backups/daily'
    daily.mkdir(parents=True)
    assert not monitor.backup_age_probe(monitor.DEFAULT_POLICY, NOW)[0]
    path = daily / 'monipan-20261024T070000Z.sql.gz'
    path.write_bytes(b'fixture')
    # A single file published before its sidecar is not a successful backup.
    assert not monitor.backup_age_probe(monitor.DEFAULT_POLICY, NOW)[0]
    path.with_name(path.name + '.sha256').write_text('a' * 64 + '  ' + path.name, encoding='ascii')
    os.utime(path, (NOW, NOW))
    assert monitor.backup_age_probe(monitor.DEFAULT_POLICY, NOW)[0]  # 25 hours.
    assert not monitor.backup_age_probe(monitor.DEFAULT_POLICY, NOW + 7200)[0]
    with pytest.raises(RuntimeError, match='clock mismatch'):
        monitor.backup_age_probe(monitor.DEFAULT_POLICY, NOW - 86400)


def test_missing_or_malformed_sidecars_are_not_success(monitor, tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, 'PROJECT', tmp_path)
    daily = tmp_path / 'backups/daily'
    daily.mkdir(parents=True)
    path = daily / 'monipan-20261025T070000Z.sql.gz'
    path.write_bytes(b'fixture')
    path.with_name(path.name + '.sha256').write_text('garbage', encoding='ascii')
    with pytest.raises(RuntimeError, match='sidecar'):
        monitor.backup_age_probe(monitor.DEFAULT_POLICY, NOW)


def test_container_probe_requests_only_allowlisted_inspection_fields(monitor, monkeypatch):
    calls = []

    def runner(args):
        calls.append(args)
        if args[:2] == ['docker', 'ps']:
            return '\n'.join(['a' * 12, 'b' * 12, 'c' * 12])
        return '\n'.join(json.dumps({'service': service, 'running': True, 'health': 'healthy'})
                         for service in ('backend', 'frontend', 'mysql'))

    monkeypatch.setattr(monitor, 'command', runner)
    assert monitor.container_probe() == (True, 'healthy')
    assert calls[1][:3] == ['docker', 'inspect', '--format']
    assert '.Config.Env' not in calls[1][3] and '.State.Health.Log' not in calls[1][3]
    monkeypatch.setattr(monitor, 'command', lambda args: '')
    assert monitor.container_probe()[0] is False


@pytest.mark.parametrize('used,free,expected', [(20, 10, True), (85, 10, False), (20, 1, False)])
def test_disk_thresholds_and_actual_docker_root(monitor, monkeypatch, used, free, expected):
    paths = []
    monkeypatch.setattr(monitor, 'command', lambda args: '/data/docker')

    def usage(path):
        paths.append(path)
        return SimpleNamespace(total=100 * 1024 ** 3, used=used * 1024 ** 3, free=free * 1024 ** 3)

    monkeypatch.setattr(monitor.shutil, 'disk_usage', usage)
    assert monitor.disk_probe(monitor.DEFAULT_POLICY)[0] is expected
    assert '/data/docker' in paths


@pytest.mark.parametrize('payload,expected', [
    (b'{"status":"ok","database":"ok"}', True),
    (b'{"status":"ok"}', False),
    (b'{"status":"unavailable","database":"ok","schema":"out_of_date"}', False)])
def test_readiness_requires_database_and_status(monitor, monkeypatch, payload, expected):
    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size):
            return payload

    monkeypatch.setattr(monitor.urllib.request, 'build_opener', lambda *args:
                        SimpleNamespace(open=lambda url, timeout: Response()))
    assert monitor.readiness_probe()[0] is expected
    assert monitor.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://bad.test') is None


@pytest.mark.parametrize('mode', ['ssl', 'starttls'])
def test_smtp_tls_before_auth_and_no_quit_retry(monitor, monkeypatch, mode):
    data, calls = config(monitor), []
    data['smtp']['security'] = mode
    data['smtp']['port'] = 465 if mode == 'ssl' else 587

    class Client:
        def __init__(self, *args, **kwargs):
            calls.append(('connect', kwargs))
        def ehlo(self):
            calls.append(('ehlo',))
        def starttls(self, context):
            assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
            calls.append(('starttls',))
        def login(self, *args):
            calls.append(('login',))
        def send_message(self, message):
            assert message['To'] == 'receiver@example.test'
            assert 'DO_NOT_LOG_AUTH_CODE' not in message.as_string()
            calls.append(('send',))
            return {}
        def close(self):
            calls.append(('close',))

    monkeypatch.setattr(monitor.smtplib, 'SMTP_SSL', Client)
    monkeypatch.setattr(monitor.smtplib, 'SMTP', Client)
    monitor.send_mail(data, '[MoniPan] 测试', 'synthetic')
    assert calls[-1][0] == 'close'
    if mode == 'starttls':
        assert [item[0] for item in calls].index('starttls') < [item[0] for item in calls].index('login')
    else:
        context = calls[0][1]['context']
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize('mutation', ['plaintext', 'header_injection', 'bad_policy', 'multiple_recipients'])
def test_invalid_configuration_refused(monitor, mutation):
    data = config(monitor)
    if mutation == 'plaintext':
        data['smtp']['security'] = 'none'
    elif mutation == 'header_injection':
        data['smtp']['recipient'] = 'me@example.test\nBcc:other@example.test'
    elif mutation == 'bad_policy':
        data['policy']['failures'] = 0
    else:
        data['smtp']['recipient'] = 'a@example.test,b@example.test'
    with pytest.raises(RuntimeError):
        monitor.validate_config(data)


def test_operator_diagnostics_are_safe_but_external_errors_stay_redacted(monitor, capsys):
    monitor.report_failure(monitor.MonitorError('Existing monitor unit requires inspection'))
    monitor.report_failure(monitor.smtplib.SMTPAuthenticationError(535, b'SECRET_AUTH_CODE me@example.test'))
    output = capsys.readouterr().out
    assert 'Existing monitor unit requires inspection' in output
    assert 'SMTPAuthenticationError' in output and 'SECRET_AUTH_CODE' not in output and '@' not in output


def test_persistence_and_corrupt_state_never_reset(monitor, tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(monitor, 'require_private', lambda path: None)  # Windows lacks Unix UID/mode.
    state, mails = monitor.empty_state(), []
    step(monitor, state, 'readiness', NOW, mails)
    step(monitor, state, 'readiness', NOW + 300, mails)
    monitor.write_json(tmp_path / 'state.json', state)
    loaded = monitor.load_state()
    step(monitor, loaded, 'readiness', NOW + 600, mails)
    assert len(mails) == 1
    (tmp_path / 'state.json').write_text('invalid JSON', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        monitor.load_state()


def test_test_mail_is_synthetic_and_does_not_touch_production_state(monitor, tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(monitor, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(monitor, 'require_private', lambda path: None)
    data, mails = config(monitor), []
    (tmp_path / 'smtp.json').write_text(json.dumps(data), encoding='utf-8')
    (tmp_path / 'state.json').write_text('existing state', encoding='utf-8')
    monkeypatch.setattr(monitor, 'send_mail', lambda conf, subject, body: mails.append((subject, body)))
    monitor.notification_test(data)
    assert len(mails) == 2 and all('TEST' in subject for subject, _ in mails)
    assert '故障' in mails[0][1] and '恢复' in mails[1][1]
    assert (tmp_path / 'state.json').read_text(encoding='utf-8') == 'existing state'
    assert (tmp_path / 'test-receipt.json').is_file()


def test_failed_test_email_never_produces_receipt(monitor, tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(monitor, 'send_mail', lambda *args: (_ for _ in ()).throw(TimeoutError('secret')))
    with pytest.raises(RuntimeError):
        monitor.notification_test(config(monitor))
    assert not (tmp_path / 'test-receipt.json').exists()


def test_artifact_units_and_python38_syntax(monitor):
    ast.parse(Path(monitor.__file__).read_text(encoding='utf-8'), feature_version=(3, 8))
    if monitor.EMBEDDED_UNITS:
        for name in monitor.NAMES:
            assert monitor.EMBEDDED_UNITS[name] == (ROOT / 'deploy/systemd' / name).read_text(encoding='utf-8')


def test_unit_does_not_depend_on_docker_or_modify_backup(monitor):
    service = (ROOT / 'deploy/systemd/monipan-monitor.service').read_text(encoding='utf-8')
    timer = (ROOT / 'deploy/systemd/monipan-monitor.timer').read_text(encoding='utf-8')
    assert 'Requires=docker' not in service
    assert 'OnUnitInactiveSec=5min' in timer
    assert 'OnActiveSec=5min' in timer and 'OnBootSec=' not in timer
    assert 'RemainAfterExit' not in service


@pytest.fixture
def installation(monitor, tmp_path, monkeypatch):
    for name, suffix in [('CONFIG_DIR', 'etc'), ('DATA_DIR', 'data'), ('APP_DIR', 'app')]:
        monkeypatch.setattr(monitor, name, tmp_path / suffix)
    units = tmp_path / 'units'
    units.mkdir()
    monkeypatch.setattr(monitor, 'UNIT_DIR', units)
    monkeypatch.setattr(monitor, 'preflight', lambda: True)
    calls = []
    monkeypatch.setattr(monitor, 'command', lambda args: calls.append(args) or '')
    monkeypatch.setattr(monitor.subprocess, 'run', lambda args, **kwargs:
                        subprocess.CompletedProcess(args, 1, stdout='not-found\n', stderr=''))
    return monitor, calls


def test_install_creates_only_monitor_files_and_keeps_timer_disabled(installation):
    monitor, calls = installation
    monitor.install()
    assert (monitor.APP_DIR / 'monitor.py').is_file()
    assert calls[-1] == ['systemctl', 'daemon-reload']
    assert not any('start' in args or 'enable' in args or 'compose' in args for args in calls)
    assert not (monitor.CONFIG_DIR / 'smtp.json').exists()


def test_existing_unit_is_never_overwritten(installation):
    monitor, calls = installation
    path = monitor.UNIT_DIR / monitor.NAMES[1]
    path.write_text('existing custom timer', encoding='utf-8')
    with pytest.raises(RuntimeError, match='Existing monitor path'):
        monitor.install()
    assert path.read_text(encoding='utf-8') == 'existing custom timer'
    assert not monitor.APP_DIR.exists() and not calls


def test_preflight_or_unit_validation_failure_does_not_install(installation, monkeypatch):
    monitor, calls = installation
    monkeypatch.setattr(monitor, 'preflight', lambda: False)
    with pytest.raises(RuntimeError, match='Preflight'):
        monitor.install()
    assert not monitor.APP_DIR.exists()
    monkeypatch.setattr(monitor, 'preflight', lambda: True)
    monkeypatch.setattr(monitor, 'command', lambda args: (_ for _ in ()).throw(RuntimeError('verify failure')))
    with pytest.raises(RuntimeError, match='verify failure'):
        monitor.install()
    assert not monitor.APP_DIR.exists()


def test_changed_smtp_config_refuses_enable_before_any_host_action(monitor, tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(monitor, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(monitor, 'require_private', lambda path: None)
    (tmp_path / 'smtp.json').write_text('changed configuration', encoding='utf-8')
    (tmp_path / 'test-receipt.json').write_text(json.dumps({
        'smtp_accepted_at': monitor.time.time(), 'config_sha256': 'old'}), encoding='utf-8')
    calls = []
    monkeypatch.setattr(monitor, 'command', lambda args: calls.append(args))
    with pytest.raises(RuntimeError, match='configuration changed'):
        monitor.enable(config(monitor))
    assert not calls


def test_first_service_failure_keeps_monitor_timer_disabled(monitor, tmp_path, monkeypatch):
    import hashlib
    import builtins
    monkeypatch.setattr(monitor, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(monitor, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(monitor, 'require_private', lambda path: None)
    data = config(monitor)
    payload = json.dumps(data).encode('utf-8')
    (tmp_path / 'smtp.json').write_bytes(payload)
    (tmp_path / 'test-receipt.json').write_text(json.dumps({
        'smtp_accepted_at': monitor.time.time(), 'config_sha256': hashlib.sha256(payload).hexdigest()}),
        encoding='utf-8')
    monkeypatch.setattr(monitor, 'preflight', lambda policy: True)
    monkeypatch.setattr(builtins, 'input', lambda *args: 'RECEIVED')
    calls = []

    def fail_start(args):
        calls.append(args)
        raise RuntimeError('simulated service failure')

    monkeypatch.setattr(monitor, 'command', fail_start)
    with pytest.raises(RuntimeError, match='service failure'):
        monitor.enable(data)
    assert calls == [['systemctl', 'start', 'monipan-monitor.service']]
