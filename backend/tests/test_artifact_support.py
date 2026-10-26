"""A clean checkout must distinguish absent archives from failed release checks."""

import pytest

from .artifact_support import archived_artifact


def test_clean_checkout_reports_absent_historical_bundle_as_skip(tmp_path, monkeypatch):
    monkeypatch.delenv('MONIPAN_REQUIRE_RELEASE_ARTIFACTS', raising=False)
    with pytest.raises(pytest.skip.Exception, match='Historical release artifact'):
        archived_artifact(tmp_path, '.docker/reviewed-release.py')


def test_strict_release_check_fails_for_an_absent_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv('MONIPAN_REQUIRE_RELEASE_ARTIFACTS', '1')
    with pytest.raises(pytest.fail.Exception, match='Historical release artifact'):
        archived_artifact(tmp_path, '.docker/reviewed-release.py')


def test_existing_bundle_is_used_without_rewriting_it(tmp_path, monkeypatch):
    monkeypatch.setenv('MONIPAN_REQUIRE_RELEASE_ARTIFACTS', '1')
    path = tmp_path / 'reviewed-release.py'
    path.write_bytes(b'unchanged historical payload')
    assert archived_artifact(tmp_path, path.name) == path
    assert path.read_bytes() == b'unchanged historical payload'
