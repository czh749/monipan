"""Package only the candidate backend bits already used in the passing rehearsal.

The resulting Docker context derives from an explicitly tagged local backend
image. It never installs or resolves packages from a network registry.
"""

import base64
import gzip
import hashlib
import io
import json
from pathlib import Path
import runpy
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
REHEARSAL_SHA256 = "a6583e9778e29c162bf9466dbec64b27e703644f1d93e7f21d9ac4e20c8ebe33"


def build():
    rehearsal = ROOT / ".docker/monipan-baseline-rehearsal-20261018.py"
    if hashlib.sha256(rehearsal.read_bytes()).hexdigest() != REHEARSAL_SHA256:
        raise RuntimeError("Rehearsal artifact differs from the version passed on MySQL")
    scope = runpy.run_path(str(rehearsal), run_name="packaging_validation")
    files = {}
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(scope["BUNDLE"]))) as bundle:
        if bundle.testzip() is not None:
            raise RuntimeError("Invalid rehearsal bundle")
        for name in bundle.namelist():
            if name.startswith("overlay/app/"):
                destination = name.removeprefix("overlay/")
                current = ROOT / "backend" / destination
                if not current.is_file() or bundle.read(name) != current.read_bytes():
                    raise RuntimeError("Candidate source changed after rehearsal: " + destination)
                files[destination] = bundle.read(name)
            elif name.startswith("vendor/"):
                files[name] = bundle.read(name)
    files["Dockerfile"] = b'''# Supply a unique local tag for the existing production backend image.
ARG MONIPAN_PARENT_IMAGE
FROM ${MONIPAN_PARENT_IMAGE}
USER root
COPY --chown=monipan:monipan vendor/ /opt/monipan-vendor/
COPY --chown=monipan:monipan app/ /app/app/
RUN chmod -R a+rX /opt/monipan-vendor /app/app
ENV PYTHONPATH=/opt/monipan-vendor
LABEL com.monipan.schema-baseline="20261018_01"
USER monipan
'''
    # Reference for keeping the checkout buildable after a reviewed source merge.
    # This file is not used for package installation by the derived image above.
    files["source-reference/requirements.txt"] = (ROOT / "backend/requirements.txt").read_bytes()
    metadata = {
        "baseline_revision": "20261018_01", "rehearsal_sha256": REHEARSAL_SHA256,
        "local_tests_passed": 209, "mysql_rehearsal_passed": True,
        "production_deployed": False, "requires_explicit_local_parent_tag": True,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    }
    files["release-manifest.json"] = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode()
    files["README.txt"] = b'''MoniPan migration baseline 20261018_01 - prepared release context

This package has NOT been deployed. Production baseline adoption is still pending.
Only backend files are included; Compose, .env, frontend, host Nginx and data are absent.
The app files and pure Python dependencies are byte-identical to the passing MySQL rehearsal.
Third-party package license files are included under vendor/.

Before deployment, inspect the current backend image, Compose labels, source hashes
and uncommitted server changes. Preserve the old image and backup modified source.
Build with --pull=false using an explicit unique LOCAL parent tag. No pip install.
Rehearse the derived image against an isolated restored MySQL before production adoption.
Back up the production DB, run checked adoption, then replace only the backend service.
Retain the prior image and an override that can select it for application rollback.
Do not downgrade or delete the baseline metadata as an automatic rollback action.
Merge app/ and source-reference/requirements.txt into the checkout only after preserving
and checking current source changes. Never reset the whole production checkout.
'''
    output = ROOT / ".docker/monipan-schema-release-20261018.tar.gz"
    with output.open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                for name, payload in sorted(files.items()):
                    item = tarfile.TarInfo(name)
                    item.size = len(payload)
                    item.mode = 0o644
                    item.mtime = 0
                    archive.addfile(item, io.BytesIO(payload))
    with tarfile.open(output, "r:gz") as archive:
        if set(archive.getnames()) != set(files):
            raise RuntimeError("Release members differ from manifest")
        for name, data in files.items():
            if archive.extractfile(name).read() != data:
                raise RuntimeError("Release content mismatch")
    print("RELEASE_CONTEXT=" + str(output))
    print("SHA256=" + hashlib.sha256(output.read_bytes()).hexdigest())
    print("BYTES=" + str(output.stat().st_size))
    return output


if __name__ == "__main__":
    build()
