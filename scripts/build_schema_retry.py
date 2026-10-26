"""Package the corrected preparation tool and guarded retained-drill retry."""

import ast
import hashlib
from pathlib import Path

from build_schema_preparation import build as build_preparation


ROOT = Path(__file__).resolve().parents[1]


def build():
    preparation = build_preparation().read_text(encoding="utf-8")
    bootstrap = ["import sys, types\n", "_release=types.ModuleType('prepare_schema_release')\n",
                 "_release.__file__='<embedded>/prepare_schema_release.py'\n",
                 "sys.modules['prepare_schema_release']=_release\n",
                 "exec(" + repr(preparation) + ",_release.__dict__)\n"]
    source = (ROOT / "scripts/retry_schema_release.py").read_text(encoding="utf-8")
    prefix_end = source.index('"""', 3) + 3
    source = source[:prefix_end] + "\n" + "".join(bootstrap) + source[prefix_end:]
    ast.parse(source, feature_version=(3, 8))
    output = ROOT / ".docker/monipan-schema-retry-20261018.py"
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(source)
    print("RETRY_ARTIFACT=" + str(output))
    print("RETRY_SHA256=" + hashlib.sha256(output.read_bytes()).hexdigest())
    print("RETRY_BYTES=" + str(output.stat().st_size))
    return output


if __name__ == "__main__":
    build()
