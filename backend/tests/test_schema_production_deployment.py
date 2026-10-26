"""Production release boundaries, using temporary files and Docker substitutes."""

import ast
import copy
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from .artifact_support import archived_artifact


ROOT = Path(__file__).resolve().parents[2]


def load_host(artifact=False):
    path = (archived_artifact(ROOT, '.docker/monipan-schema-deploy-20261025.py')
            if artifact else ROOT / 'scripts/deploy_schema_release.py')
    spec = importlib.util.spec_from_file_location('schema_production_deployment', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=[False, True], ids=['source', 'upload-artifact'])
def release_fixture(tmp_path, monkeypatch, request):
    host = load_host(request.param)
    project = tmp_path / 'project'
    project.mkdir()
    release_root = project / 'backups/schema-release-existing'
    context = release_root / 'permission-retry/context'
    context.mkdir(parents=True)
    monkeypatch.setattr(host, 'PROJECT', project)
    monkeypatch.setattr(host, 'STATE_PATH', release_root / 'release-state.json')
    old_image = 'sha256:' + '1' * 64
    old_tag = 'monipan-backend:retained'
    old_sources = {'backend/app/main.py': b'old main\n', 'backend/requirements.txt': b'old dependencies\n'}
    new_sources = {'app/main.py': b'new main\n', 'app/schema.py': b'new migration\n',
                   'source-reference/requirements.txt': b'new dependencies\n'}
    for name, data in old_sources.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name, data in new_sources.items():
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {'files': {name: hashlib.sha256(data).hexdigest() for name, data in new_sources.items()}}
    (context / 'release-manifest.json').write_text(json.dumps(manifest))
    monkeypatch.setattr(host, 'EXPECTED_MANIFEST', manifest)
    for name, data in {'compose.yaml': b'preserve compose', '.env': b'preserve-secret-value',
                       'scripts/backup_mysql.sh': b'reviewed backup'}.items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setattr(host, 'BACKUP_SCRIPT_SHA256', host.digest(project / 'scripts/backup_mysql.sh'))
    old_hashes = {name: hashlib.sha256(data).hexdigest() for name, data in old_sources.items()}
    old_hashes['backend/app/schema.py'] = None
    events = []
    settings = {'fail_switch': False, 'fail_rollback': False, 'fail_smoke': False, 'fail_adopt': False}
    alias = {'monipan-backend': old_image, old_tag: old_image}
    services = {}
    for name, letter in zip(('backend', 'frontend', 'mysql'), 'abc'):
        services[name] = {
            'Id': letter * 64, 'Image': old_image if name == 'backend' else 'sha256:' + letter * 64,
            'State': {'Running': True, 'Health': {'Status': 'healthy'}},
            'Config': {'Image': 'monipan-backend', 'User': 'monipan',
                       'Labels': {'com.docker.compose.project': 'monipan', 'com.docker.compose.service': name},
                       'Env': ['MONIPAN_ENVIRONMENT=production', 'MONIPAN_COOKIE_SECURE=1',
                               'MONIPAN_DATABASE_URL=mysql+pymysql://user:synthetic-secret@mysql/monipan']},
            'HostConfig': {'PortBindings': {'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8080'}]}}
        }
    initial_ids = {name: value['Id'] for name, value in services.items()}
    configuration = {'services': {'backend': {'build': {'context': str(project / 'backend')},
                      'healthcheck': {'test': ['python', '/readyz']}}}}
    state = {'status': 'candidate_image_verified_production_not_deployed', 'production_deployed': False,
             'production_schema_modified': False, 'project': str(project), 'old_image': old_image,
             'old_image_tag': old_tag, 'candidate_image': host.CANDIDATE, 'candidate_context': str(context),
             'production_container_ids': initial_ids,
             'original_file_hashes': {name: host.digest(project / name) for name in
                                     ('compose.yaml', '.env', *old_sources)},
             'release_context_sha256': 'reviewed-context', 'candidate_report': {
                 'status': 'passed', 'dialect': 'mysql', 'baseline_revision': host.BASELINE,
                 'candidate_files_verified': 154, 'business_rows_unchanged_by_adoption': True,
                 'source_overlay_applied': False}}
    host.STATE_PATH.write_text(json.dumps(state))
    def verify_unchanged(project_arg, originals, files):
        events.append('verify_unchanged')
        assert project_arg == project
        host.require({name: item['Id'] for name, item in services.items()} ==
                     {name: item['Id'] for name, item in originals.items()}, 'Concurrent service change')
        for name, expected in files.items():
            host.require(host.digest(project / name) == expected, 'Configuration or source changed')
    def image(identifier):
        identity = alias.get(identifier, identifier)
        return {'Id': identity, 'Config': {'User': 'monipan',
                'Labels': {'com.monipan.schema-baseline': host.BASELINE}}}
    def command(args, **kwargs):
        events.append(('command', args))
        if args[:3] == ['docker', 'image', 'tag']:
            alias[args[4]] = args[3]
            return ''
        if args[:2] == ['docker', 'exec']:
            if args[3] == 'python':
                return json.dumps({name[len('backend/'):]: value for name, value in old_hashes.items()})
            assert args[3:] in (['nginx', '-t'], ['nginx', '-s', 'reload'])
            return ''
        if args[:2] == ['/bin/sh', 'scripts/backup_mysql.sh']:
            destination = Path(kwargs['env']['MONIPAN_BACKUP_DIR']) / 'monipan-test.sql.gz'
            destination.write_bytes(gzip.compress(b'synthetic database backup'))
            Path(str(destination) + '.sha256').write_text(host.digest(destination) + '  ' + destination.name)
            return ''
        if args[:2] == ['gzip', '-t']:
            gzip.decompress(Path(args[2]).read_bytes())
            return ''
        raise AssertionError(args)
    def compose(project_arg, *args, **kwargs):
        events.append(('compose', args))
        assert project_arg == project
        if args[:1] == ('config',):
            return json.dumps(configuration)
        if args[:1] == ('ps',):
            return services[args[-1]]['Id']
        assert args[:1] == ('up',)
        assert args[-1] == 'backend' and '--no-build' in args and '--no-deps' in args
        assert args[args.index('--pull') + 1] == 'never'
        selected = alias['monipan-backend']
        if selected == host.CANDIDATE and settings['fail_switch']:
            raise RuntimeError('Synthetic candidate startup failure')
        if selected == old_image and settings['fail_rollback']:
            raise RuntimeError('Synthetic rollback failure')
        services['backend']['Image'] = selected
        services['backend']['Id'] = ('d' if selected == host.CANDIDATE else 'e') * 64
        return ''
    def schema(backend, action, **kwargs):
        events.append(('schema', action))
        if action == 'adopt' and settings['fail_adopt']:
            raise RuntimeError('Synthetic uncertain adoption')
        return {'status': 'baseline_compatible', 'ready_to_stamp': True, 'schema_versions': []} if action == 'check' else {
            'schema_versions': [host.BASELINE], 'business_schema_modified': False}
    def smoke(base, **kwargs):
        events.append(('smoke', base))
        if settings['fail_smoke'] and base == host.PUBLIC_URL and services['backend']['Image'] == host.CANDIDATE:
            raise RuntimeError('Synthetic public smoke failure')
        return [{'path': '/readyz', 'status': 200}]
    def check_backup(path):
        assert Path(str(path) + '.sha256').read_text().split()[0] == host.digest(path)
        return host.digest(path)
    release = SimpleNamespace(RELEASE_SHA256='reviewed-context', PARENT_IMAGE=old_image,
                              production_containers=lambda project: copy.deepcopy(services),
                              verify_unchanged=verify_unchanged)
    monkeypatch.setattr(host, 'helpers', lambda: {'release': release, 'check_backup': check_backup})
    monkeypatch.setattr(host, 'image', image)
    monkeypatch.setattr(host, 'command', command)
    monkeypatch.setattr(host, 'compose', compose)
    monkeypatch.setattr(host, 'schema_probe', schema)
    monkeypatch.setattr(host, 'smoke', smoke)
    monkeypatch.setattr(host, 'inspect_container', lambda identifier: next(
        copy.deepcopy(item) for item in services.values() if item['Id'] == identifier))
    monkeypatch.setattr(host.shutil, 'disk_usage', lambda path: SimpleNamespace(free=10 * 1024 ** 3))
    return SimpleNamespace(host=host, project=project, state=state, services=services, events=events,
                           settings=settings, alias=alias, old_sources=old_sources, initial_ids=initial_ids,
                           configuration=configuration, old_image=old_image)


def test_preparation_creates_fresh_backup_and_keeps_production_unchanged(release_fixture):
    f = release_fixture
    path = f.host.prepare()
    plan = json.loads(path.read_text())
    assert plan['status'] == 'prepared_production_not_modified'
    assert Path(plan['backup']).parent == path.parent / 'database'
    assert (path.parent / 'configuration-backup/.env').read_bytes() == b'preserve-secret-value'
    assert (path.parent / 'source-backup/backend/app/main.py').read_bytes() == b'old main\n'
    assert f.alias['monipan-backend'] == f.old_image
    assert ('schema', 'adopt') not in f.events
    assert not any(event[0] == 'compose' and event[1][0] == 'up' for event in f.events if isinstance(event, tuple))
    assert {name: item['Id'] for name, item in f.services.items()} == f.initial_ids
    assert (f.project / 'backend/app/main.py').read_bytes() == b'old main\n'
    assert 'backend/app/main.py' not in plan['configuration_hashes']


@pytest.mark.parametrize('damage', ['candidate', 'state', 'report', 'source', 'backup_script'])
def test_preparation_refuses_drift_before_any_database_write(release_fixture, damage):
    f = release_fixture
    state = json.loads(f.host.STATE_PATH.read_text())
    if damage == 'candidate':
        state['candidate_image'] = f.old_image
    elif damage == 'state':
        state['production_schema_modified'] = True
    elif damage == 'report':
        state['candidate_report']['source_overlay_applied'] = True
    elif damage == 'source':
        (f.project / 'backend/app/main.py').write_bytes(b'unknown operator changes')
    else:
        (f.project / 'scripts/backup_mysql.sh').write_bytes(b'unknown backup')
    f.host.STATE_PATH.write_text(json.dumps(state))
    with pytest.raises(RuntimeError):
        f.host.prepare()
    assert ('schema', 'adopt') not in f.events
    assert f.alias['monipan-backend'] == f.old_image


def test_deploy_registers_baseline_before_switching_only_backend(release_fixture):
    f = release_fixture
    path = f.host.prepare()
    f.host.deploy(path)
    plan = json.loads(path.read_text())
    assert plan['status'] == 'production_deployed_verified'
    assert f.events.index(('schema', 'adopt')) < next(i for i, event in enumerate(f.events)
        if isinstance(event, tuple) and event[0] == 'command' and event[1][:3] == ['docker', 'image', 'tag'])
    assert f.services['backend']['Image'] == f.host.CANDIDATE
    assert f.services['frontend']['Id'] == f.initial_ids['frontend']
    assert f.services['mysql']['Id'] == f.initial_ids['mysql']
    assert (f.project / 'backend/app/main.py').read_bytes() == b'new main\n'
    assert (f.project / 'backend/app/schema.py').read_bytes() == b'new migration\n'
    assert (f.project / '.env').read_bytes() == b'preserve-secret-value'
    assert json.loads(f.host.STATE_PATH.read_text())['production_deployed'] is True


@pytest.mark.parametrize('damage', ['expired', 'future', 'backup', 'config', 'source'])
def test_deploy_refuses_invalid_preparation_before_adoption(release_fixture, damage):
    f = release_fixture
    path = f.host.prepare()
    plan = json.loads(path.read_text())
    if damage in ('expired', 'future'):
        offset = -3 if damage == 'expired' else 1
        plan['created_at_utc'] = (datetime.now(timezone.utc) + timedelta(hours=offset)).isoformat()
        f.host.save_json(path, plan)
    elif damage == 'backup':
        Path(plan['backup']).write_bytes(b'damaged backup')
    elif damage == 'config':
        (f.project / 'compose.yaml').write_bytes(b'operator configuration change')
    else:
        (f.project / 'backend/app/main.py').write_bytes(b'operator source change')
    with pytest.raises((AssertionError, RuntimeError)):
        f.host.deploy(path)
    assert ('schema', 'adopt') not in f.events
    assert f.services['backend']['Image'] == f.old_image


def test_uncertain_adoption_never_switches_or_downgrades_database(release_fixture, capsys):
    f = release_fixture
    path = f.host.prepare()
    f.settings['fail_adopt'] = True
    with pytest.raises(RuntimeError):
        f.host.deploy(path)
    plan = json.loads(path.read_text())
    assert plan['status'] == 'deployment_failed' and not plan['backend_switch_started']
    assert f.alias['monipan-backend'] == f.old_image
    assert 'BASELINE_ADOPTION_REQUIRES_INSPECTION_BACKEND_UNCHANGED' in capsys.readouterr().out


@pytest.mark.parametrize('failure', ['fail_switch', 'fail_smoke'])
def test_failed_release_rolls_back_image_and_sources_without_database_downgrade(release_fixture, failure):
    f = release_fixture
    path = f.host.prepare()
    f.settings[failure] = True
    with pytest.raises(RuntimeError):
        f.host.deploy(path)
    plan = json.loads(path.read_text())
    assert plan['status'] == 'rolled_back_baseline_retained'
    assert plan['production_baseline_registered'] is True
    assert f.services['backend']['Image'] == f.old_image
    assert f.alias['monipan-backend'] == f.old_image
    for name, data in f.old_sources.items():
        assert (f.project / name).read_bytes() == data
    assert not (f.project / 'backend/app/schema.py').exists()
    assert f.services['frontend']['Id'] == f.initial_ids['frontend']
    assert f.services['mysql']['Id'] == f.initial_ids['mysql']
    assert all(event[1] in ('check', 'adopt', 'status') for event in f.events
               if isinstance(event, tuple) and event[0] == 'schema')
    assert Path(plan['backup']).is_file()


def test_rollback_failure_is_reported_and_keeps_all_backups(release_fixture, capsys):
    f = release_fixture
    path = f.host.prepare()
    f.settings.update(fail_switch=True, fail_rollback=True)
    with pytest.raises(RuntimeError):
        f.host.deploy(path)
    plan = json.loads(path.read_text())
    assert plan['status'] == 'rollback_failed_requires_attention'
    assert Path(plan['backup']).is_file()
    assert 'ROLLBACK_FAILED_REQUIRES_ATTENTION' in capsys.readouterr().out


def test_manual_rollback_refuses_to_overwrite_new_operator_source(release_fixture):
    f = release_fixture
    path = f.host.prepare()
    f.host.deploy(path)
    target = f.project / 'backend/app/main.py'
    target.write_bytes(b'new operator edit')
    with pytest.raises(RuntimeError, match='Source changed outside'):
        f.host.rollback(path, json.loads(path.read_text()))
    assert target.read_bytes() == b'new operator edit'
    assert f.services['backend']['Image'] == f.host.CANDIDATE


def test_changed_mysql_container_blocks_deployment_before_adoption(release_fixture):
    f = release_fixture
    path = f.host.prepare()
    f.services['mysql']['Id'] = 'f' * 64
    with pytest.raises(RuntimeError, match='containers changed'):
        f.host.deploy(path)
    assert ('schema', 'adopt') not in f.events
    assert f.services['backend']['Image'] == f.old_image


@pytest.mark.parametrize('field,value', [('image_reference', 'mysql:latest'), ('old_image', 'sha256:' + '2' * 64)])
def test_plan_cannot_retag_unrelated_images_or_replace_the_rollback_target(release_fixture, field, value):
    f = release_fixture
    path = f.host.prepare()
    plan = json.loads(path.read_text())
    plan[field] = value
    f.host.save_json(path, plan)
    with pytest.raises(RuntimeError, match='identity changed'):
        f.host.deploy(path)
    assert ('schema', 'adopt') not in f.events


def test_future_schema_blocks_manual_rollback_before_source_or_image_changes(release_fixture, monkeypatch):
    f = release_fixture
    path = f.host.prepare()
    f.host.deploy(path)
    def wrong_schema(*args, **kwargs):
        raise RuntimeError('Production baseline version was not confirmed')
    monkeypatch.setattr(f.host, 'schema_probe', wrong_schema)
    with pytest.raises(RuntimeError, match='baseline version'):
        f.host.rollback(path, json.loads(path.read_text()))
    assert f.alias['monipan-backend'] == f.host.CANDIDATE
    assert (f.project / 'backend/app/main.py').read_bytes() == b'new main\n'


def test_schema_operation_uses_candidate_and_keeps_credentials_out_of_arguments(monkeypatch):
    host = load_host()
    host.EXPECTED_MANIFEST = {'files': {}}
    calls = []
    def command(args, **kwargs):
        calls.append((args, kwargs))
        assert args[-2:] == [host.CANDIDATE, '-']
        assert args[args.index('--network') + 1] == 'container:' + 'a' * 64
        assert '--rm' in args and '--read-only' in args
        assert '--volume' not in args and '--mount' not in args and '--privileged' not in args
        assert not any('synthetic-secret' in arg for arg in args)
        assert kwargs['env']['MONIPAN_DISABLE_MARKET_LOOP'] == '1'
        source = kwargs['input'].decode()
        ast.parse(source, feature_version=(3, 9))
        assert 'SET SESSION TRANSACTION READ ONLY' in source and 'app.main' not in source
        return 'PRODUCTION_SCHEMA_RESULT=' + json.dumps({'ready_to_stamp': True, 'schema_versions': []})
    monkeypatch.setattr(host, 'command', command)
    host.schema_probe({'Id': 'a' * 64, 'Config': {'Env': [
        'MONIPAN_DATABASE_URL=mysql+pymysql://user:synthetic-secret@mysql/monipan']}}, 'check')
    assert len(calls) == 1


def test_compose_command_cannot_rebuild_or_recreate_protected_services(monkeypatch, tmp_path):
    host = load_host()
    calls = []
    monkeypatch.setattr(host, 'command', lambda args, **kwargs: calls.append((args, kwargs)) or '')
    host.compose(tmp_path, 'up', '-d', '--no-deps', '--no-build', '--pull', 'never', 'backend')
    args, kwargs = calls[0]
    assert args[:6] == ['docker', 'compose', '--project-name', 'monipan', '--file', str(tmp_path / 'compose.yaml')]
    assert args[-1] == 'backend' and '--no-deps' in args and '--no-build' in args
    assert kwargs['env']['COMPOSE_PROJECT_NAME'] == 'monipan'


@pytest.mark.parametrize('damage', [None, 'security_headers', 'readiness', 'origin'])
def test_http_smoke_checks_expected_errors_readiness_and_https_headers(monkeypatch, damage):
    host = load_host()
    calls = []
    class Response:
        def __init__(self, url, status, body, headers):
            self.url, self.code, self.body, self.headers = url, status, body, headers
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def geturl(self):
            return self.url
        def read(self, limit):
            return self.body[:limit]
    def urlopen(request, **kwargs):
        calls.append(request.full_url)
        path = request.full_url[len(host.PUBLIC_URL):]
        statuses = {'/': 200, '/readyz': 200, '/api/auth/me': 401, '/docs': 404}
        headers = {'Strict-Transport-Security': 'max-age=31536000',
                   'Content-Security-Policy': "default-src 'self'", 'X-Content-Type-Options': 'nosniff',
                   'X-Frame-Options': 'DENY'}
        if damage == 'security_headers':
            headers.pop('Strict-Transport-Security')
        body = json.dumps({'database': 'unavailable' if damage == 'readiness' else 'ok'}).encode()
        if statuses[path] >= 400:
            raise host.urllib.error.HTTPError(request.full_url, statuses[path], 'expected', headers, io.BytesIO(body))
        url = 'http://unexpected.invalid/' if damage == 'origin' else request.full_url
        return Response(url, statuses[path], body, headers)
    monkeypatch.setattr(host.urllib.request, 'urlopen', urlopen)
    if damage:
        with pytest.raises(RuntimeError):
            host.smoke(host.PUBLIC_URL, public=True)
    else:
        assert [item['status'] for item in host.smoke(host.PUBLIC_URL, public=True)] == [200, 200, 401, 404]
        assert len(calls) == 4


def test_paths_cannot_escape_project(tmp_path):
    with pytest.raises(RuntimeError, match='Unsafe relative'):
        load_host().checked_path(tmp_path, '../outside')


def test_generated_artifact_import_does_not_run_release_actions():
    artifact = archived_artifact(ROOT, '.docker/monipan-schema-deploy-20261025.py')
    ast.parse(artifact.read_text(encoding='utf-8'), feature_version=(3, 8))
    scope = runpy.run_path(str(artifact), run_name='artifact_validation')
    assert scope['_helpers'] is None
    assert scope['EXPECTED_MANIFEST']['baseline_revision'] == '20261018_01'
    assert scope['BACKUP_SCRIPT_SHA256'] == hashlib.sha256((ROOT / 'scripts/backup_mysql.sh').read_bytes()).hexdigest()
