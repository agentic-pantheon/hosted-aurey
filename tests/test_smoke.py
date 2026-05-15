"""Smoke tests for package import and version metadata."""

import aurey


def test_package_import_and_version():
    assert isinstance(aurey.__version__, str)
    assert aurey.__version__
