"""Build one self-contained, reviewable upload for the server web terminal."""

import ast
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def build():
    source = (ROOT / 'scripts/monitor_production.py').read_text(encoding='utf-8')
    units = {name: (ROOT / 'deploy/systemd' / name).read_text(encoding='utf-8')
             for name in ('monipan-monitor.service', 'monipan-monitor.timer')}
    source = source.replace('EMBEDDED_UNITS = None', 'EMBEDDED_UNITS = ' + repr(units), 1)
    ast.parse(source, feature_version=(3, 8))
    target = ROOT / '.docker/monipan-monitor-20261025.py'
    target.parent.mkdir(exist_ok=True)
    with target.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(source)
    print('ARTIFACT=' + str(target))
    print('SHA256=' + hashlib.sha256(target.read_bytes()).hexdigest())
    print('BYTES=' + str(target.stat().st_size))
    return target


if __name__ == '__main__':
    build()
