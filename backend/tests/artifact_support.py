"""Historical production bundles are optional locally, mandatory in release checks.

The source regressions always run. Exact reviewed upload bundles intentionally
stay outside Git; missing bundles are visible skips unless strict mode is set.
"""

import os

import pytest


def archived_artifact(root, relative):
    path = root / relative
    if path.is_file():
        return path
    message = ('Historical release artifact is not present: ' + relative
               + '; see docs/git-organization-2026-10-26.md')
    if os.environ.get('MONIPAN_REQUIRE_RELEASE_ARTIFACTS') == '1':
        pytest.fail(message, pytrace=False)
    pytest.skip(message)
