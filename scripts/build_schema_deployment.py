"""Build the staged production tool without changing the rehearsed app bits."""

import ast
import hashlib
import json
from pathlib import Path
import tarfile


ROOT = Path(__file__).resolve().parents[1]
CONTEXT_SHA256 = '3896cd7cb5d61a69b1ea16d662fb8f9e47244f52adecd266e2c6c7e4448db052'


def build():
    archive_path = ROOT / '.docker/monipan-schema-release-20261018.tar.gz'
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != CONTEXT_SHA256:
        raise RuntimeError('The verified release context changed')
    with tarfile.open(archive_path, 'r:gz') as archive:
        manifest = json.load(archive.extractfile('release-manifest.json'))
        for name, expected in manifest['files'].items():
            if hashlib.sha256(archive.extractfile(name).read()).hexdigest() != expected:
                raise RuntimeError('Release context content changed')
    source = (ROOT / 'scripts/deploy_schema_release.py').read_text(encoding='utf-8')
    source = source.replace('EXPECTED_MANIFEST = None', 'EXPECTED_MANIFEST = ' + repr(manifest), 1)
    backup_hash = hashlib.sha256((ROOT / 'scripts/backup_mysql.sh').read_bytes()).hexdigest()
    source = source.replace('BACKUP_SCRIPT_SHA256 = None', 'BACKUP_SCRIPT_SHA256 = ' + repr(backup_hash), 1)
    ast.parse(source, feature_version=(3, 8))
    output = ROOT / '.docker/monipan-schema-deploy-20261025.py'
    with output.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(source)
    print('DEPLOYMENT_ARTIFACT=' + str(output))
    print('DEPLOYMENT_SHA256=' + hashlib.sha256(output.read_bytes()).hexdigest())
    print('DEPLOYMENT_BYTES=' + str(output.stat().st_size))
    return output


if __name__ == '__main__':
    build()
