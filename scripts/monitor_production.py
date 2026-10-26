"""Small, standalone host monitor. Linux Python 3.8+, standard library only.

The uploaded artifact embeds the two systemd units; it never edits the app,
Compose, .env, MySQL, Nginx, or the existing backup units.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import smtplib
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request


PROJECT = Path('/opt/monipan')
CONFIG_DIR = Path('/etc/monipan-monitor')
DATA_DIR = Path('/var/lib/monipan-monitor')
APP_DIR = Path('/usr/local/lib/monipan-monitor')
UNIT_DIR = Path('/etc/systemd/system')
NAMES = ('monipan-monitor.service', 'monipan-monitor.timer')
EMBEDDED_UNITS = None
DEFAULT_POLICY = {'failures': 2, 'recoveries': 2, 'reminder_seconds': 86400,
                  'disk_used_percent': 85, 'disk_free_gib': 2, 'backup_max_hours': 26}
LABELS = {'containers': '生产容器', 'edge': '宿主机 Nginx', 'readiness': '应用与数据库就绪检查',
          'disk': '磁盘空间', 'backup_timer': '自动备份定时器',
          'backup_result': '最近备份任务', 'backup_age': '备份文件时效'}


class MonitorError(RuntimeError):
    """An operator-facing diagnostic built only from our own safe messages."""


def require(condition, message):
    if not condition:
        raise MonitorError(message)


def log(event, **fields):
    print(json.dumps(dict(event=event, **fields), ensure_ascii=False), flush=True)


def report_failure(exc):
    fields = {'error_type': type(exc).__name__}
    if isinstance(exc, MonitorError):
        fields['reason'] = str(exc)
    log('MONITOR_FAILED', **fields)


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=10,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C',
                                 'SYSTEMD_COLORS': '0', 'DOCKER_HOST': 'unix:///var/run/docker.sock'})
    # Never include subprocess stdout/stderr in an exception.
    require(result.returncode == 0, 'Host command failed')
    return result.stdout.strip()


def properties(unit):
    output = command(['systemctl', 'show', unit, '--no-pager', '-p', 'LoadState',
                      '-p', 'ActiveState', '-p', 'UnitFileState', '-p', 'Result',
                      '-p', 'ExecMainStatus'])
    return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)


def container_probe():
    # Compose labels select existing production containers without loading .env.
    ids = command(['docker', 'ps', '-a', '--filter', 'label=com.docker.compose.project=monipan',
                   '--filter', 'label=com.docker.compose.oneoff=False', '--format', '{{.ID}}']).split()
    require(all(re.fullmatch(r'[0-9a-f]{12,64}', item) for item in ids), 'Invalid container IDs')
    # Only these fields leave Docker; healthcheck logs and environment never do.
    template = ('{"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                '"running":{{json .State.Running}},'
                '"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}"missing"{{end}}}')
    rows = [json.loads(line) for line in command(
        ['docker', 'inspect', '--format', template] + ids).splitlines()] if ids else []
    failures = []
    for name in ('backend', 'frontend', 'mysql'):
        selected = [row for row in rows if row.get('service') == name]
        if len(selected) != 1:
            failures.append(name + ':missing_or_multiple')
        elif selected[0].get('running') is not True or selected[0].get('health') != 'healthy':
            failures.append(name + ':not_healthy')
    return not failures, ','.join(failures) or 'healthy'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def readiness_probe():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open('http://127.0.0.1:8080/readyz', timeout=8) as response:
        payload = response.read(4097)
        require(response.status == 200 and len(payload) <= 4096, 'Invalid readiness response')
        data = json.loads(payload)
        ok = data.get('status') == 'ok' and data.get('database') == 'ok'
        return ok, 'ready' if ok else 'not_ready'


def disk_probe(policy):
    docker_root = command(['docker', 'info', '--format', '{{.DockerRootDir}}'])
    require(docker_root.startswith('/') and '\n' not in docker_root, 'Invalid Docker root')
    failures = []
    for path in sorted(set(('/', str(PROJECT), str(PROJECT / 'backups/daily'), docker_root))):
        usage = shutil.disk_usage(path)
        percent = 100 * usage.used / usage.total
        free_gib = usage.free / (1024 ** 3)
        if percent >= policy['disk_used_percent'] or free_gib < policy['disk_free_gib']:
            # Paths and raw Docker output do not go into email.
            failures.append('used=%.1f%% free=%.2fGiB' % (percent, free_gib))
    return not failures, '; '.join(sorted(set(failures))) or 'space_ok'


def backup_age_probe(policy, now):
    daily = PROJECT / 'backups/daily'
    require(daily.is_dir() and not daily.is_symlink(), 'Backup directory unavailable')
    completed = []
    for path in daily.glob('monipan-*.sql.gz'):
        match = re.fullmatch(r'monipan-(\d{8}T\d{6}Z)\.sql\.gz', path.name)
        if not match or path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            continue
        sidecar = path.with_name(path.name + '.sha256')
        if not sidecar.is_file() or sidecar.is_symlink():
            continue  # A pair is published atomically by two separate renames.
        require(sidecar.stat().st_size < 512, 'Invalid backup sidecar')
        fields = sidecar.read_text(encoding='ascii').split()
        require(len(fields) == 2 and re.fullmatch(r'[0-9a-f]{64}', fields[0])
                and fields[1] == path.name, 'Invalid backup sidecar')
        stamp = datetime.strptime(match.group(1), '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).timestamp()
        require(stamp <= now + 300 and path.stat().st_mtime <= now + 300, 'Backup clock mismatch')
        completed.append(stamp)
    if not completed:
        return False, 'no_completed_backup_pair'
    age = (now - max(completed)) / 3600
    return age <= policy['backup_max_hours'], 'age_hours=%.2f' % age


def collect(policy, now):
    results = {}

    def probe(key, fn):
        try:
            ok, detail = fn()
            results[key] = {'ok': bool(ok), 'detail': detail}
        except Exception:
            # Unexpected response bodies, credentials, or command output cannot leak.
            results[key] = {'ok': False, 'detail': 'probe_failed'}

    probe('containers', container_probe)
    probe('edge', lambda: (properties('nginx.service').get('ActiveState') == 'active', 'nginx_active_required'))
    probe('readiness', readiness_probe)
    probe('disk', lambda: disk_probe(policy))

    def timer():
        data = properties('monipan-backup.timer')
        return (data.get('LoadState') == 'loaded' and data.get('ActiveState') == 'active'
                and data.get('UnitFileState') == 'enabled'), 'timer_enabled_active_required'

    def backup_result():
        data = properties('monipan-backup.service')
        running = data.get('ActiveState') in ('active', 'activating')
        ok = data.get('LoadState') == 'loaded' and (running or (
            data.get('Result') == 'success' and data.get('ExecMainStatus') == '0'))
        return ok, 'running' if running else ('success' if ok else 'last_run_failed')

    probe('backup_timer', timer)
    probe('backup_result', backup_result)
    probe('backup_age', lambda: backup_age_probe(policy, now))
    return results


def private_path(path):
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'Symlink path refused')


def require_private(path):
    private_path(path)
    info = path.stat()
    require(info.st_uid == 0 and info.st_mode & 0o077 == 0, 'Expected root-owned private path')


def write_json(path, data, exclusive=False):
    private_path(path)
    require_private(path.parent)
    if exclusive:
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            os.chmod(path, 0o600)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.monitor-', dir=str(path.parent))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path):
    require_private(path.parent)
    require_private(path)
    require(path.is_file() and path.stat().st_size < 65536, 'Invalid private JSON file')
    return json.loads(path.read_text(encoding='utf-8'))


def validate_config(config):
    smtp = config['smtp']
    require(config.get('version') == 1, 'Unsupported configuration version')
    require(smtp.get('security') in ('ssl', 'starttls'), 'SMTP TLS is required')
    require(type(smtp.get('port')) is int and 1 <= smtp['port'] <= 65535, 'Invalid SMTP port')
    for field in ('host', 'username', 'password', 'sender', 'recipient'):
        require(isinstance(smtp.get(field), str) and bool(smtp[field])
                and not any(c in smtp[field] for c in '\r\n\x00'), 'Invalid SMTP field')
    for field in ('sender', 'recipient'):
        require(bool(re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+', smtp[field])), 'Use one plain email address')
    policy = config.get('policy', {})
    limits = {'failures': (1, 10), 'recoveries': (1, 10), 'reminder_seconds': (3600, 604800),
              'disk_used_percent': (50, 99), 'disk_free_gib': (0.1, 1000), 'backup_max_hours': (24, 72)}
    require(set(policy) == set(DEFAULT_POLICY), 'Invalid monitoring policy fields')
    for key, (low, high) in limits.items():
        require(type(policy[key]) in (int, float) and low <= policy[key] <= high, 'Invalid monitoring threshold')
    require(type(policy['failures']) is int and type(policy['recoveries']) is int,
            'Sample thresholds must be integers')
    return config


def load_config():
    return validate_config(read_json(CONFIG_DIR / 'smtp.json'))


def send_mail(config, subject, body):
    smtp = config['smtp']
    message = EmailMessage()
    message['From'] = smtp['sender']
    message['To'] = smtp['recipient']
    message['Subject'] = subject
    message['Date'] = formatdate(localtime=False)
    message['Message-ID'] = make_msgid(domain='monipan.top')
    message.set_content(body)
    context = ssl.create_default_context()
    if smtp['security'] == 'ssl':
        client = smtplib.SMTP_SSL(smtp['host'], smtp['port'], timeout=10, context=context)
    else:
        client = smtplib.SMTP(smtp['host'], smtp['port'], timeout=10)
    try:
        if smtp['security'] == 'starttls':
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        client.login(smtp['username'], smtp['password'])
        refused = client.send_message(message)
        require(not refused, 'SMTP recipient refused')
    finally:
        # A failed QUIT after successful DATA does not turn a delivered message
        # into a retry. Network ambiguity before DATA acknowledgement can duplicate.
        client.close()


def empty_state():
    return {'version': 1, 'checks': {}, 'mute_until': 0, 'last_run': 0}


def load_state():
    path = DATA_DIR / 'state.json'
    if not path.exists():
        return empty_state()
    state = read_json(path)
    require(state.get('version') == 1 and isinstance(state.get('checks'), dict), 'Invalid monitor state')
    return state


def advance(state, results, policy, now, sender):
    """Evaluate samples and send one aggregate message, retrying on later ticks."""
    due = []
    state['last_run'] = now
    for key in LABELS:
        result = results[key]
        item = state['checks'].setdefault(key, {'bad': 0, 'good': 0, 'active': False,
                                               'mailed': False, 'last_sent': 0,
                                               'retry_at': 0, 'send_failures': 0})
        item['detail'] = result['detail']
        item['bad'] = min(10, item['bad'] + 1) if not result['ok'] else 0
        item['good'] = min(10, item['good'] + 1) if result['ok'] else 0
        if item['bad'] >= policy['failures']:
            item['active'] = True
        if item['good'] >= policy['recoveries']:
            item['active'] = False
        kind = None
        if item['active'] and not result['ok']:
            if not item['mailed']:
                kind = '故障'
            elif now - item['last_sent'] >= policy['reminder_seconds']:
                kind = '持续故障'
        elif not item['active'] and item['mailed'] and item['good'] >= policy['recoveries']:
            kind = '恢复'
        if kind and now >= item['retry_at'] and now >= state.get('mute_until', 0):
            due.append((key, kind))
        elif not item['active'] and not item['mailed'] and result['ok']:
            item['retry_at'] = 0
            item['send_failures'] = 0
    if not due:
        return True
    lines = ['MoniPan 基础监测', 'UTC: ' + datetime.fromtimestamp(now, timezone.utc).isoformat()]
    lines.extend('%s：%s（%s）' % (kind, LABELS[key], results[key]['detail']) for key, kind in due)
    lines.append('请通过腾讯云网页终端查看；邮件不包含业务数据或凭据。')
    try:
        sender('[MoniPan] ' + ('恢复' if all(kind == '恢复' for _, kind in due) else '运行异常'), '\n'.join(lines))
    except Exception as exc:
        for key, _ in due:
            item = state['checks'][key]
            item['send_failures'] = min(5, item['send_failures'] + 1)
            item['retry_at'] = now + min(3600, 300 * 2 ** (item['send_failures'] - 1))
        log('SMTP_SEND_FAILED', checks=[key for key, _ in due], error_type=type(exc).__name__)
        return False
    for key, kind in due:
        item = state['checks'][key]
        item.update(mailed=kind != '恢复', last_sent=now, retry_at=0, send_failures=0)
    log('SMTP_ACCEPTED', events=[key + ':' + kind for key, kind in due])
    return True


@contextmanager
def locked():
    import fcntl
    require_private(DATA_DIR)
    private_path(DATA_DIR / 'monitor.lock')
    with (DATA_DIR / 'monitor.lock').open('a') as stream:
        os.chmod(stream.name, 0o600)
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def preflight(policy=None):
    require(PROJECT.is_dir() and not PROJECT.is_symlink(), 'Production project is missing or symlinked')
    result = collect(policy or DEFAULT_POLICY, time.time())
    log('PREFLIGHT_READ_ONLY', checks=result)
    return all(row['ok'] for row in result.values())


def install():
    # No overwrite, no application/DB restart, no backup execution.
    targets = (CONFIG_DIR, DATA_DIR, APP_DIR, *(UNIT_DIR / name for name in NAMES))
    for path in targets:
        private_path(path)
        require(not path.exists(), 'Existing monitor path requires inspection: ' + str(path))
    for name in NAMES:
        # systemctl show of a nonexistent unit may exit nonzero across versions.
        result = subprocess.run(['systemctl', 'show', name, '-p', 'LoadState', '--value'],
                                capture_output=True, text=True, timeout=10)
        require(result.stdout.strip() == 'not-found', 'Existing monitor unit requires inspection')
    require(preflight(), 'Preflight contains abnormalities; inspect before installation')
    units = EMBEDDED_UNITS
    if units is None:
        folder = Path(__file__).resolve().parents[1] / 'deploy/systemd'
        units = {name: (folder / name).read_text(encoding='utf-8') for name in NAMES}
    require(set(units) == set(NAMES), 'Unexpected monitor units')
    with tempfile.TemporaryDirectory(prefix='monipan-monitor-') as directory:
        paths = []
        for name in NAMES:
            path = Path(directory) / name
            path.write_text(units[name], encoding='utf-8')
            paths.append(str(path))
        command(['systemd-analyze', 'verify'] + paths)
    for path in (CONFIG_DIR, DATA_DIR, APP_DIR):
        path.mkdir(mode=0o700)
    source = Path(__file__).read_bytes()
    target = APP_DIR / 'monitor.py'
    with target.open('xb') as stream:
        stream.write(source)
    target.chmod(0o600)
    for name in NAMES:
        with (UNIT_DIR / name).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(units[name])
        (UNIT_DIR / name).chmod(0o644)
    command(['systemctl', 'daemon-reload'])
    log('MONITOR_INSTALLED_TIMER_DISABLED', script_sha256=hashlib.sha256(source).hexdigest())


def configure():
    require_private(CONFIG_DIR)
    require(not (CONFIG_DIR / 'smtp.json').exists(), 'SMTP configuration already exists; edit the private file')
    require(sys.stdin.isatty(), 'Configure in the interactive web terminal')
    host = input('SMTP host (from your mailbox provider): ').strip()
    security = input('TLS mode [ssl/starttls, default ssl]: ').strip() or 'ssl'
    port = int(input('SMTP port [default 465, STARTTLS usually 587]: ').strip()
               or ('465' if security == 'ssl' else '587'))
    username = input('SMTP login / sender email: ').strip()
    recipient = input('Recipient email [default sender]: ').strip() or username
    password = getpass.getpass('SMTP authorization code (hidden): ')
    config = {'version': 1, 'smtp': {'host': host, 'port': port, 'security': security,
                                   'username': username, 'password': password,
                                   'sender': username, 'recipient': recipient},
              'policy': DEFAULT_POLICY.copy()}
    # Validate before storing; never print configuration or an exception value.
    validate_config(config)
    write_json(CONFIG_DIR / 'smtp.json', config, exclusive=True)
    log('SMTP_CONFIGURED_PRIVATE')


def notification_test(config):
    # Use the same state machine with synthetic samples; production state is untouched.
    state = empty_state()
    results = {key: {'ok': True, 'detail': 'synthetic_test'} for key in LABELS}
    results['readiness']['ok'] = False
    sender = lambda subject, body: send_mail(config, '[测试 TEST] ' + subject,
                                            '合成事件，未停止生产服务。\n' + body)
    now = time.time()
    for index in range(int(config['policy']['failures'])):
        require(advance(state, results, config['policy'], now + index, sender), 'Test SMTP failed')
    results['readiness']['ok'] = True
    for index in range(int(config['policy']['recoveries'])):
        require(advance(state, results, config['policy'], now + 60 + index, sender), 'Test SMTP failed')
    write_json(DATA_DIR / 'test-receipt.json', {'smtp_accepted_at': time.time(),
               'config_sha256': hashlib.sha256((CONFIG_DIR / 'smtp.json').read_bytes()).hexdigest()})
    log('TEST_TWO_MESSAGES_SMTP_ACCEPTED_VERIFY_INBOX')


def enable(config):
    receipt = read_json(DATA_DIR / 'test-receipt.json')
    require(0 <= time.time() - receipt['smtp_accepted_at'] <= 86400, 'Repeat notification test before enablement')
    require(receipt['config_sha256'] == hashlib.sha256((CONFIG_DIR / 'smtp.json').read_bytes()).hexdigest(),
            'SMTP configuration changed; repeat notification test')
    require(preflight(config['policy']), 'Preflight contains abnormalities')
    # Human confirmation is about inbox receipt, not another authorization prompt.
    require(input('After seeing BOTH TEST failure and recovery emails, type RECEIVED: ').strip() == 'RECEIVED',
            'Inbox delivery is not confirmed')
    command(['systemctl', 'start', NAMES[0]])
    command(['systemctl', 'enable', '--now', NAMES[1]])
    require(command(['systemctl', 'is-active', NAMES[1]]) == 'active', 'Monitor timer not active')
    require(command(['systemctl', 'is-enabled', NAMES[1]]) == 'enabled', 'Monitor timer not enabled')
    log('MONITOR_TIMER_ENABLED', interval_minutes=5, failures=config['policy']['failures'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('preflight', 'install', 'configure', 'test-mail',
                                          'enable', 'check', 'mute', 'status'), nargs='?', default='preflight')
    parser.add_argument('--minutes', type=int, default=30, help='Mute 0-1440 minutes; 0 resumes notifications')
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'Use root in the Linux server web terminal')
    os.umask(0o077)
    if args.action == 'preflight':
        return 0 if preflight() else 1
    if args.action == 'install':
        install()
        return 0
    if args.action == 'configure':
        configure()
        return 0
    config = load_config()
    if args.action == 'enable':
        enable(config)  # Starting the service must not hold its file lock.
        return 0
    with locked():
        if args.action == 'test-mail':
            notification_test(config)
            return 0
        state = load_state()
        if args.action == 'mute':
            require(0 <= args.minutes <= 1440, 'Mute must be between 0 and 1440 minutes')
            state['mute_until'] = time.time() + args.minutes * 60
            write_json(DATA_DIR / 'state.json', state)
            log('MUTE_UPDATED', minutes=args.minutes)
            return 0
        if args.action == 'status':
            log('MONITOR_STATUS', last_run=state['last_run'], mute_until=state.get('mute_until', 0),
                checks=state['checks'])
            return 0
        now = time.time()
        results = collect(config['policy'], now)
        sent = advance(state, results, config['policy'], now,
                       lambda subject, body: send_mail(config, subject, body))
        # Persist retry state even when SMTP fails, and never discard a corrupt state.
        write_json(DATA_DIR / 'state.json', state)
        log('CHECK_FINISHED', checks=results, notification_ok=sent)
        return 0 if sent else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # No exception text or traceback: SMTP responses may contain private data.
        report_failure(exc)
        raise SystemExit(1)
