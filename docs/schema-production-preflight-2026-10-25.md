# 生产迁移接入前盘点：2026-10-25

本页为生产发布前的只读盘点快照。候选镜像 `sha256:89a426a296110d25c620a68caae2253c4b8ef71551f8ea5696ee3718a92fcd46` 在当时已通过隔离检查，生产尚未接入；后续本日已完成生产基线登记和正式发布，当前状态见 [生产发布记录](schema-production-release-2026-10-25.md)。

**本次只读盘点已执行通过**，用户已回传 `PRODUCTION_PREFLIGHT_OK_READ_ONLY`，记录见 `evidence/schema-production-preflight-2026-10-25.json`。下面命令保留作历史依据，无需重复；后续 prepare 和正式 deploy 也已通过。

当时的盘点目标是读取成功状态、旧镜像保留标签、Compose 和网络配置，为受控发布准备。下面历史命令只调用已校验交付件中的读取与检查函数，不调用其重试主函数，不创建或启动候选应用，不修改生产数据库或配置，也不输出完整环境变量、数据库连接串或完整 Compose 配置。

## 在当前 root 终端执行

无需再次上传文件。粘贴下面完整命令并回传输出：

```sh
python3 - <<'PY'
import hashlib, json, os, runpy
from pathlib import Path

artifact = Path('/home/ubuntu/monipan-schema-retry-20261025.py')
expected = '8009fc49c962dacc5351e1e559bae70feb08f0b3f13b5d8a3cfc3d9b5aa6b4c9'
if hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
    raise SystemExit('STOP: uploaded artifact checksum mismatch')
scope = runpy.run_path(str(artifact), run_name='production_read_only_inspection')
release = scope['release']
state_path = scope['STATE_PATH']
original_state = state_path.read_bytes()
state = json.loads(original_state)
release.require(state['status'] == 'candidate_image_verified_production_not_deployed'
                and state.get('production_deployed') is False
                and state.get('production_schema_modified') is False,
                'Unexpected release state; stop before production changes')
release.require(state['candidate_image'] == 'sha256:89a426a296110d25c620a68caae2253c4b8ef71551f8ea5696ee3718a92fcd46',
                'Candidate image changed')
project = Path(state['project']).resolve(strict=True)
services = release.production_containers(project)
release.require({k: v['Id'] for k, v in services.items()} == state['production_container_ids'],
                'Production containers changed after the rehearsal')
release.verify_unchanged(project, services, state['original_file_hashes'])
old_image = json.loads(release.command(['docker', 'image', 'inspect', state['old_image_tag']]))[0]
candidate = json.loads(release.command(['docker', 'image', 'inspect', state['candidate_image']]))[0]
release.require(old_image['Id'] == state['old_image'] and candidate['Id'] == state['candidate_image']
                and candidate['Config'].get('User') == 'monipan'
                and candidate['Config'].get('Labels', {}).get('com.monipan.schema-baseline') == '20261018_01',
                'Retained rollback or candidate image does not match')
compose_version = release.command(['docker', 'compose', 'version', '--short'])
config = json.loads(release.command(['docker', 'compose', 'config', '--format', 'json'], cwd=project,
                                  env=dict(os.environ, COMPOSE_PROJECT_NAME='monipan')))
backend = config['services']['backend']
report = {k: state.get(k) for k in ('status', 'old_image', 'old_image_tag', 'candidate_image',
                                  'candidate_image_tag', 'candidate_context', 'release_context_sha256')}
report.update(compose_version=compose_version, production_containers={
    name: {'id': item['Id'], 'health': item['State'].get('Health', {}).get('Status'),
           'networks': list(item['NetworkSettings']['Networks'])}
    for name, item in services.items()}, backend_compose={
        'image': backend.get('image'), 'has_build': bool(backend.get('build')),
        'has_custom_command': bool(backend.get('command')), 'has_ports': bool(backend.get('ports')),
        'volumes': [v.get('target') for v in backend.get('volumes', [])],
        'healthcheck_uses_readyz': '/readyz' in json.dumps(backend.get('healthcheck', {})),
        'stop_grace_period': backend.get('stop_grace_period')})
release.verify_unchanged(project, services, state['original_file_hashes'])
release.require(state_path.read_bytes() == original_state, 'Release state changed during inspection')
print(json.dumps(report, indent=2))
print('PRODUCTION_PREFLIGHT_OK_READ_ONLY')
PY
```

`run_name` 不为 `__main__`，因此加载交付文件不会执行其镜像构建或重试入口。脚本仍会连接现有 backend 的数据库读取是否已有版本表，不会触发旧应用生命周期或隐式迁移。失败时先回传错误，不修改发布状态来绕过检查。

## 盘点后的发布顺序

1. 明确维护窗口及用户通知，核对候选和回滚镜像的不可变 ID，保留当前源码与配置快照。
2. 创建并校验新的生产数据库备份，不能使用一周前的演练备份代替发布前备份。
3. 使用已验证候选镜像检查当前生产结构，通过后登记 `20261018_01`；不重建业务表、不删除历史索引。
4. 以生产当前配置为基础，仅覆盖 backend 镜像进行更新；不整体覆盖本地 Compose、不重建 MySQL 或数据卷。
5. 检查新 backend 的迁移版本、readiness、API、公网 HTTPS、安全头及业务冒烟。检查 frontend Nginx 是否仍解析到旧 backend 地址；如需刷新解析，先语法检查再 reload，保留 frontend 容器及配置。
6. 失败时切回兼容的旧 backend 镜像并复核入口，不自动删除基线版本或降级业务表。完整回退动作仍需在正式执行前准备和验收。

以上为当时的后续发布安排。当前正常生产发布已通过，实际失败回退仍待演练；不能仅凭这份只读盘点记录宣称完整发布与回滚流程全部完成。
