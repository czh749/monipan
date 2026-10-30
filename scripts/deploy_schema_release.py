"""Prepare, deploy, or roll back the rehearsed MoniPan baseline release.

The default action only snapshots files, creates a fresh backup, and checks the
production schema read-only. Deployment is a separate explicit action.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import secrets
import shutil
import urllib.error
import urllib.request


RETRY_PATH = Path('/home/ubuntu/monipan-schema-retry-20261025.py')
RETRY_SHA256 = '8009fc49c962dacc5351e1e559bae70feb08f0b3f13b5d8a3cfc3d9b5aa6b4c9'
CANDIDATE = 'sha256:89a426a296110d25c620a68caae2253c4b8ef71551f8ea5696ee3718a92fcd46'
STATE_PATH = Path('/opt/monipan/backups/schema-release-20261018-kn9o5neq/release-state.json')
PROJECT = Path('/opt/monipan')
BASELINE = '20261018_01'
PUBLIC_URL = 'https://monipan.top'
EXPECTED_MANIFEST = None
BACKUP_SCRIPT_SHA256 = None
_helpers = None


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def helpers():
    global _helpers
    if _helpers is None:
        require(hashlib.sha256(RETRY_PATH.read_bytes()).hexdigest() == RETRY_SHA256,
                'Previously verified retry artifact changed')
        _helpers = runpy.run_path(str(RETRY_PATH), run_name='deployment_helpers_only')
    return _helpers


def command(args, **kwargs):
    return helpers()['release'].command(args, **kwargs)


def inspect_container(identifier):
    return json.loads(command(['docker', 'inspect', identifier]))[0]


def image(identifier):
    return json.loads(command(['docker', 'image', 'inspect', identifier]))[0]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def checked_path(root, relative):
    require(not Path(relative).is_absolute() and '..' not in Path(relative).parts,
            'Unsafe relative source path')
    target = root / relative
    for part in (root, *target.relative_to(root).parents, target):
        check = part if part.is_absolute() else root / part
        require(not check.is_symlink(), 'Symbolic links are not allowed in release paths')
    return target


def save_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def compose(project, *args, timeout=60):
    return command(['docker', 'compose', '--project-name', 'monipan',
                    '--file', str(project / 'compose.yaml'), *args],
                   cwd=project, env=dict(os.environ, COMPOSE_PROJECT_NAME='monipan'), timeout=timeout)


def ready_state():
    state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    release = helpers()['release']
    require(state.get('status') == 'candidate_image_verified_production_not_deployed'
            and state.get('production_deployed') is False
            and state.get('production_schema_modified') is False
            and state.get('candidate_image') == CANDIDATE
            and state.get('release_context_sha256') == release.RELEASE_SHA256,
            'Expected the verified candidate before production adoption')
    report = state.get('candidate_report', {})
    require(report.get('status') == 'passed' and report.get('dialect') == 'mysql'
            and report.get('baseline_revision') == BASELINE
            and report.get('candidate_files_verified') == 154
            and report.get('business_rows_unchanged_by_adoption') is True
            and report.get('source_overlay_applied') is False,
            'Final-image MySQL rehearsal evidence is incomplete')
    require(Path(state['project']).resolve(strict=True) == PROJECT, 'Production project path changed')
    services = release.production_containers(PROJECT)
    require(state.get('old_image') == services['backend']['Image'] == release.PARENT_IMAGE,
            'Retained rollback image is not the running pre-release image')
    require({name: item['Id'] for name, item in services.items()} == state['production_container_ids'],
            'Production containers changed since the rehearsal')
    release.verify_unchanged(PROJECT, services, state['original_file_hashes'])
    require(image(state['old_image_tag'])['Id'] == state['old_image'], 'Rollback image tag changed')
    candidate = image(CANDIDATE)
    require(candidate['Id'] == CANDIDATE and candidate['Config'].get('User') == 'monipan'
            and candidate['Config'].get('Labels', {}).get('com.monipan.schema-baseline') == BASELINE,
            'Candidate image configuration changed')
    return state, services


def validate_configuration(services):
    config = json.loads(compose(PROJECT, 'config', '--format', 'json'))
    backend = config['services']['backend']
    require(not backend.get('image') and backend.get('build')
            and not backend.get('command') and not backend.get('entrypoint')
            and not backend.get('volumes') and not backend.get('ports')
            and '/readyz' in json.dumps(backend.get('healthcheck', {})),
            'Backend configuration differs from the inspected build-only service')
    reference = services['backend']['Config'].get('Image')
    require(reference in ('monipan-backend', 'monipan-backend:latest'),
            'Unexpected default backend image reference; inspect before retagging')
    require(image(reference)['Id'] == services['backend']['Image'], 'Default backend image tag drifted')
    env = dict(entry.split('=', 1) for entry in services['backend']['Config']['Env'])
    require(env.get('MONIPAN_ENVIRONMENT') == 'production' and env.get('MONIPAN_COOKIE_SECURE') == '1'
            and env.get('MONIPAN_DATABASE_URL', '').startswith('mysql+pymysql://'),
            'Running backend production settings are incomplete')
    bindings = services['frontend']['HostConfig'].get('PortBindings', {}).get('80/tcp', [])
    require(len(bindings) == 1 and bindings[0].get('HostIp') == '127.0.0.1'
            and str(bindings[0].get('HostPort', '')).isdigit(), 'Frontend loopback binding changed')
    return reference, 'http://127.0.0.1:' + bindings[0]['HostPort'], env


def context_sources(state):
    require(EXPECTED_MANIFEST is not None, 'Use the generated deployment artifact')
    context = Path(state['candidate_context']).resolve(strict=True)
    require(context.is_relative_to(STATE_PATH.parent) if hasattr(context, 'is_relative_to')
            else str(context).startswith(str(STATE_PATH.parent) + os.sep), 'Candidate context is outside the release')
    require(json.loads((context / 'release-manifest.json').read_text()) == EXPECTED_MANIFEST,
            'Candidate manifest differs from the reviewed release')
    for name, expected in EXPECTED_MANIFEST['files'].items():
        require(digest(checked_path(context, name)) == expected, 'Candidate context file changed: ' + name)
    sources = {'backend/' + name: context / name for name in EXPECTED_MANIFEST['files'] if name.startswith('app/')}
    sources['backend/requirements.txt'] = context / 'source-reference/requirements.txt'
    return sources


def source_snapshot(plan_root, sources, backend):
    # Compare every file to the running old image before overwriting checkout files.
    names = [name[len('backend/'):] for name in sources]
    probe = ('import hashlib,json\nfrom pathlib import Path\nnames=' + repr(names) +
             '\nprint(json.dumps({n:hashlib.sha256((Path("/app")/n).read_bytes()).hexdigest() '
             'if (Path("/app")/n).is_file() else None for n in names}))\n')
    expected_old = json.loads(command(['docker', 'exec', backend['Id'], 'python', '-c', probe]))
    hashes = {}
    for name in sources:
        target = checked_path(PROJECT, name)
        old_hash = digest(target)
        require(old_hash == expected_old[name[len('backend/'):]],
                'Checkout source differs from the running backend: ' + name)
        hashes[name] = old_hash
        if old_hash is not None:
            saved = checked_path(plan_root / 'source-backup', name)
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(target.read_bytes())
            saved.chmod(0o600)
    return hashes


def schema_probe(backend, action, *, network_container=None):
    require(action in ('check', 'adopt', 'status'), 'Unexpected schema action')
    expected_files = {name: value for name, value in EXPECTED_MANIFEST['files'].items()
                      if name.startswith(('app/', 'vendor/'))}
    source = '''import hashlib,json,os
from pathlib import Path
try:
    expected = ''' + repr(expected_files) + '''
    for name,value in expected.items():
        path=Path('/app')/name if name.startswith('app/') else Path('/opt/monipan-vendor')/name[len('vendor/'):]
        if hashlib.sha256(path.read_bytes()).hexdigest()!=value:
            raise RuntimeError('Candidate file mismatch')
    from app.database import engine
    from app.schema import baseline_check,adopt_baseline,current_revisions,head_revision
    if engine.dialect.name!='mysql' or head_revision()!='20261018_01':
        raise RuntimeError('Unexpected database or migration head')
    action = ''' + repr(action) + '''
    if action=='adopt':
        report=adopt_baseline(engine)
        if report.get('schema_versions')!=['20261018_01'] or report.get('business_schema_modified') is not False:
            raise RuntimeError('Baseline write could not be confirmed')
    else:
        with engine.connect() as connection:
            connection.exec_driver_sql('SET SESSION TRANSACTION READ ONLY')
            versions=list(current_revisions(connection))
            if action=='check':
                if versions:
                    raise RuntimeError('Production version is already present; inspect before adoption')
                checked=baseline_check(connection)
                report={'ready_to_stamp':checked['ready_to_stamp'],'status':checked['status'],
                        'schema_versions':versions,'adoption_problems':checked['adoption_problems']}
            else:
                report={'status':'current','schema_versions':versions}
    print('PRODUCTION_SCHEMA_RESULT='+json.dumps(report))
except Exception as exc:
    print('PRODUCTION_SCHEMA_RESULT='+json.dumps({'status':'failed','error_type':type(exc).__name__}))
    raise SystemExit(1)
'''
    values = dict(entry.split('=', 1) for entry in backend['Config']['Env'])
    env = dict(os.environ, MONIPAN_DATABASE_URL=values['MONIPAN_DATABASE_URL'],
               MONIPAN_ENVIRONMENT='production', MONIPAN_COOKIE_SECURE='1', MONIPAN_DISABLE_MARKET_LOOP='1')
    token = secrets.token_hex(12)
    args = ['docker', 'run', '--rm', '-i', '--pull=never', '--name', 'monipan-schema-operation-' + token,
            '--label', 'com.monipan.schema-operation=' + token,
            '--network', 'container:' + (network_container or backend['Id']),
            '--read-only', '--memory', '512m', '--memory-swap', '512m', '--cpus', '0.5', '--pids-limit', '128',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--entrypoint', 'python']
    for key in ('MONIPAN_DATABASE_URL', 'MONIPAN_ENVIRONMENT', 'MONIPAN_COOKIE_SECURE', 'MONIPAN_DISABLE_MARKET_LOOP'):
        args += ['--env', key]
    output = command(args + [CANDIDATE, '-'], input=source.encode('utf-8'), env=env, timeout=180)
    matches = [line.split('=', 1)[1] for line in output.splitlines() if line.startswith('PRODUCTION_SCHEMA_RESULT=')]
    require(len(matches) == 1, 'Schema probe did not return one result')
    report = json.loads(matches[0])
    if action == 'check':
        require(report.get('ready_to_stamp') is True and report.get('schema_versions') == [],
                'Production schema requires review; no baseline was registered')
    else:
        require(report.get('schema_versions') == [BASELINE], 'Production baseline version was not confirmed')
    return report


def assert_files(plan, *, restored=False):
    for name, original in plan['configuration_hashes'].items():
        require(digest(checked_path(PROJECT, name)) == original, 'Production configuration changed: ' + name)
    if restored:
        for name, original in plan['source_hashes'].items():
            require(digest(checked_path(PROJECT, name)) == original, 'Original source changed: ' + name)


def verify_backup(plan):
    path = Path(plan['backup'])
    require(helpers()['check_backup'](path) == plan['backup_sha256'], 'Release backup changed')
    command(['gzip', '-t', str(path)], timeout=120)


def prepare():
    state, services = ready_state()
    reference, local_url, _ = validate_configuration(services)
    sources = context_sources(state)
    require(digest(PROJECT / 'scripts/backup_mysql.sh') == BACKUP_SCRIPT_SHA256,
            'Production backup script differs from the reviewed version')
    require(shutil.disk_usage(PROJECT).free >= 1024 ** 3, 'At least 1 GiB free disk is required')
    root = STATE_PATH.parent / ('production-20261025-' + secrets.token_hex(12))
    root.mkdir(mode=0o700)
    plan_path = root / 'deployment-plan.json'
    plan = {'status': 'preparing', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'candidate_image': CANDIDATE, 'old_image': state['old_image'], 'old_image_tag': state['old_image_tag'],
            'image_reference': reference, 'local_url': local_url, 'project': str(PROJECT),
            'candidate_context': state['candidate_context'],
            'configuration_hashes': {name: value for name, value in state['original_file_hashes'].items()
                                     if name not in sources},
            'original_containers': {name: item['Id'] for name, item in services.items()},
            'production_baseline_registered': False, 'backend_switch_started': False}
    save_json(plan_path, plan)
    print('DEPLOYMENT_PLAN=' + str(plan_path), flush=True)
    try:
        plan['source_hashes'] = source_snapshot(root, sources, services['backend'])
        plan['new_source_hashes'] = {name: digest(path) for name, path in sources.items()}
        for name, value in plan['configuration_hashes'].items():
            if value is not None:
                original = checked_path(PROJECT, name)
                require(digest(original) == value, 'Configuration changed before snapshot')
                saved = checked_path(root / 'configuration-backup', name)
                saved.parent.mkdir(parents=True, exist_ok=True)
                saved.write_bytes(original.read_bytes())
                saved.chmod(0o600)
        backup_dir = root / 'database'
        backup_dir.mkdir(mode=0o700)
        print('FRESH_PRODUCTION_BACKUP_STARTING', flush=True)
        command(['/bin/sh', 'scripts/backup_mysql.sh'], cwd=PROJECT, timeout=600,
                env=dict(os.environ, COMPOSE_PROJECT_NAME='monipan', MONIPAN_BACKUP_DIR=str(backup_dir)))
        backups = list(backup_dir.glob('monipan-*.sql.gz'))
        require(len(backups) == 1, 'Expected exactly one new production backup')
        plan.update(backup=str(backups[0]), backup_sha256=helpers()['check_backup'](backups[0]))
        verify_backup(plan)
        print('PRODUCTION_SCHEMA_READ_ONLY_CHECK_STARTING', flush=True)
        plan['production_schema_check'] = schema_probe(services['backend'], 'check')
        helpers()['release'].verify_unchanged(PROJECT, services, state['original_file_hashes'])
        assert_files(plan, restored=True)
        plan['status'] = 'prepared_production_not_modified'
        save_json(plan_path, plan)
    except Exception as exc:
        plan.update(status='preparation_failed', error_type=type(exc).__name__)
        save_json(plan_path, plan)
        raise
    print('DEPLOYMENT_PLAN=' + str(plan_path), flush=True)
    print('FRESH_BACKUP=' + plan['backup'], flush=True)
    print('FRESH_BACKUP_SHA256=' + plan['backup_sha256'], flush=True)
    print('PRODUCTION_RELEASE_PREPARED_NOT_DEPLOYED', flush=True)
    return plan_path


def load_plan(path):
    path = path.resolve(strict=True)
    require(path.parent.parent == STATE_PATH.parent and path.name == 'deployment-plan.json'
            and re.fullmatch(r'production-20261025-[0-9a-f]{24}', path.parent.name), 'Unexpected deployment plan location')
    plan = json.loads(path.read_text(encoding='utf-8'))
    require(plan['candidate_image'] == CANDIDATE and plan['project'] == str(PROJECT)
            and plan['old_image'] == helpers()['release'].PARENT_IMAGE
            and plan['image_reference'] in ('monipan-backend', 'monipan-backend:latest'),
            'Deployment plan identity changed')
    require(Path(plan['backup']).parent == path.parent / 'database', 'Release backup is outside this plan')
    return path, plan


def live_services(plan, *, expected_image=None):
    result = {}
    for name in ('backend', 'frontend', 'mysql'):
        identifier = compose(PROJECT, 'ps', '--all', '-q', name)
        require(bool(re.fullmatch(r'[0-9a-f]{12,64}', identifier)), 'Expected one ' + name + ' container')
        item = inspect_container(identifier)
        labels = item['Config'].get('Labels', {})
        require(labels.get('com.docker.compose.project') == 'monipan'
                and labels.get('com.docker.compose.service') == name, 'Service identity mismatch')
        if name != 'backend':
            require(item['Id'] == plan['original_containers'][name]
                    and item['State'].get('Health', {}).get('Status') == 'healthy', 'Protected service changed: ' + name)
        result[name] = item
    if expected_image:
        require(result['backend']['Image'] == expected_image
                and result['backend']['State'].get('Health', {}).get('Status') == 'healthy',
                'Backend image or readiness does not match the release')
    return result


def sync_sources(plan_path, plan, *, restore=False):
    sources = context_sources({'candidate_context': plan['candidate_context']})
    for name in sources:
        target = checked_path(PROJECT, name)
        current = digest(target)
        require(current in (plan['source_hashes'][name], plan['new_source_hashes'][name]),
                'Source changed outside this release: ' + name)
        if restore:
            expected = plan['source_hashes'][name]
            if expected is None:
                if target.exists():
                    target.unlink()
                continue
            source = checked_path(plan_path.parent / 'source-backup', name)
        else:
            expected = plan['new_source_hashes'][name]
            source = sources[name]
        require(digest(source) == expected, 'Source snapshot checksum mismatch')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.schema-release-' + secrets.token_hex(8) + '.tmp')
        require(not temporary.exists() and not temporary.is_symlink(), 'Temporary source path already exists')
        with temporary.open('xb') as stream:
            stream.write(source.read_bytes())
        temporary.chmod(0o644)
        os.replace(temporary, target)
    plan['sources_synchronized'] = not restore
    save_json(plan_path, plan)


def switch_backend(plan, desired_image):
    require(desired_image in (CANDIDATE, plan['old_image']), 'Unexpected deployment image')
    require(image(desired_image)['Id'] == desired_image, 'Deployment image is unavailable')
    command(['docker', 'image', 'tag', desired_image, plan['image_reference']])
    require(image(plan['image_reference'])['Id'] == desired_image, 'Default image reference was not updated')
    compose(PROJECT, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', '--force-recreate',
            '--wait', '--wait-timeout', '120', 'backend', timeout=600)
    return live_services(plan, expected_image=desired_image)


def reload_frontend(services):
    command(['docker', 'exec', services['frontend']['Id'], 'nginx', '-t'])
    command(['docker', 'exec', services['frontend']['Id'], 'nginx', '-s', 'reload'])


def smoke(base, *, public=False):
    results = []
    for path, expected in (('/', 200), ('/readyz', 200), ('/api/auth/me', 401), ('/docs', 404)):
        request = urllib.request.Request(base + path, headers={'User-Agent': 'MoniPan-Release-Check/1'})
        try:
            response = urllib.request.urlopen(request, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status, headers = response.code, response.headers
            body = response.read(1024 * 1024)
            require(status == expected, 'Unexpected HTTP status for ' + path)
            require(not public or response.geturl().startswith(PUBLIC_URL + '/'), 'Public HTTPS redirect changed origin')
            if path == '/readyz':
                require(json.loads(body).get('database') == 'ok', 'Database readiness failed')
            if public:
                hsts = re.search(r'(?:^|;)\s*max-age=(\d+)', headers.get('Strict-Transport-Security', ''), re.I)
                require(hsts and int(hsts.group(1)) >= 31536000
                        and headers.get('Content-Security-Policy')
                        and headers.get('X-Content-Type-Options') == 'nosniff'
                        and headers.get('X-Frame-Options'), 'Required public security headers are missing')
            results.append({'path': path, 'status': status})
    return results


def update_release(plan, status):
    state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    require(state.get('candidate_image') == CANDIDATE, 'Release state identity changed')
    state.update(status=status, production_schema_modified=True,
                 production_baseline_registered=bool(plan.get('production_baseline_registered')),
                 production_deployed=status == 'production_deployed_verified')
    save_json(STATE_PATH, state)


def rollback(plan_path, plan):
    require(plan.get('backend_switch_started'), 'No backend switch was started by this plan')
    assert_files(plan)
    current = live_services(plan)
    require(current['backend']['Image'] in (CANDIDATE, plan['old_image']), 'An unrelated backend release is running')
    require(image(plan['old_image_tag'])['Id'] == plan['old_image'], 'Rollback image tag changed')
    schema_probe(current['backend'], 'status', network_container=current['mysql']['Id'])
    # Rollback only the application and its checked-out sources; never downgrade the DB.
    sync_sources(plan_path, plan, restore=True)
    services = switch_backend(plan, plan['old_image'])
    reload_frontend(services)
    plan['rollback_local_smoke'] = smoke(plan['local_url'])
    plan['rollback_public_smoke'] = smoke(PUBLIC_URL, public=True)
    schema_probe(services['backend'], 'status')
    assert_files(plan, restored=True)
    plan['status'] = 'rolled_back_baseline_retained'
    save_json(plan_path, plan)
    update_release(plan, 'production_rolled_back_baseline_retained')
    print('ROLLBACK_OK_BASELINE_RETAINED', flush=True)


def deploy(plan_path):
    plan_path, plan = load_plan(plan_path)
    require(plan['status'] == 'prepared_production_not_modified', 'This plan is not awaiting deployment')
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(plan['created_at_utc'])).total_seconds()
    require(0 <= age <= 7200, 'Preparation is older than two hours; create a fresh backup and plan')
    state, services = ready_state()
    require(plan['old_image_tag'] == state['old_image_tag']
            and plan['candidate_context'] == state['candidate_context'], 'Prepared release identity changed')
    require(plan['original_containers'] == {name: item['Id'] for name, item in services.items()},
            'Production containers changed after preparation')
    reference, local_url, _ = validate_configuration(services)
    require(reference == plan['image_reference'] and local_url == plan['local_url'], 'Deployment configuration changed')
    assert_files(plan, restored=True)
    verify_backup(plan)
    context_sources(state)
    schema_probe(services['backend'], 'check')
    try:
        plan['status'] = 'adopting_production_baseline'
        save_json(plan_path, plan)
        print('PRODUCTION_BASELINE_REGISTRATION_STARTING', flush=True)
        plan['adoption_report'] = schema_probe(services['backend'], 'adopt')
        plan['production_baseline_registered'] = True
        save_json(plan_path, plan)
        update_release(plan, 'production_baseline_registered_backend_pending')
        print('PRODUCTION_BASELINE_REGISTERED', flush=True)
        plan['backend_switch_started'] = True
        plan['status'] = 'switching_backend'
        save_json(plan_path, plan)
        sync_sources(plan_path, plan)
        print('BACKEND_SWITCH_STARTING', flush=True)
        services = switch_backend(plan, CANDIDATE)
        reload_frontend(services)
        schema_probe(services['backend'], 'status')
        plan['local_smoke'] = smoke(local_url)
        plan['public_smoke'] = smoke(PUBLIC_URL, public=True)
        assert_files(plan)
        live_services(plan, expected_image=CANDIDATE)
        plan['status'] = 'production_deployed_verified'
        save_json(plan_path, plan)
        update_release(plan, 'production_deployed_verified')
    except Exception as exc:
        plan.update(status='deployment_failed', error_type=type(exc).__name__)
        save_json(plan_path, plan)
        if plan.get('backend_switch_started'):
            try:
                rollback(plan_path, plan)
            except Exception as rollback_error:
                plan.update(status='rollback_failed_requires_attention', rollback_error_type=type(rollback_error).__name__)
                save_json(plan_path, plan)
                print('ROLLBACK_FAILED_REQUIRES_ATTENTION', flush=True)
        else:
            print('BASELINE_ADOPTION_REQUIRES_INSPECTION_BACKEND_UNCHANGED', flush=True)
        raise
    print('DEPLOYMENT_PLAN=' + str(plan_path), flush=True)
    print('PRODUCTION_BACKEND_IMAGE=' + CANDIDATE, flush=True)
    print('PRODUCTION_ALEMBIC_RELEASE_VERIFIED', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', choices=('prepare', 'deploy', 'rollback'), default='prepare')
    parser.add_argument('--plan', type=Path)
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0, 'Run as root on the existing Linux server')
    require(EXPECTED_MANIFEST is not None and BACKUP_SCRIPT_SHA256 is not None, 'Use the generated deployment artifact')
    require(PROJECT.resolve(strict=True) == PROJECT and STATE_PATH.resolve(strict=True) == STATE_PATH,
            'Project and release state paths must not contain symbolic links')
    require(args.action == 'prepare' or args.plan is not None, 'Deploy/rollback requires a prepared --plan')
    import fcntl
    os.umask(0o077)
    with Path('/run/lock/monipan-schema-prepare.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another schema release operation is running') from None
        if args.action == 'prepare':
            prepare()
        elif args.action == 'deploy':
            deploy(args.plan)
        else:
            path, plan = load_plan(args.plan)
            rollback(path, plan)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        detail = str(exc) if type(exc) is RuntimeError else type(exc).__name__
        print('PRODUCTION_RELEASE_FAILED ' + detail, flush=True)
        raise SystemExit(1)
