"""Fase 22 — versionamento central + checksums de release (herméticos)."""

import importlib.util
from pathlib import Path

from app.core.config import REPO_ROOT, settings

VERSION_FILE = REPO_ROOT / "VERSION"


def _version_from_root() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def _import_release_checksums():
    path = REPO_ROOT / "scripts" / "release_checksums.py"
    spec = importlib.util.spec_from_file_location("release_checksums_f22", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Versão central (fonte única)
# ---------------------------------------------------------------------------

def test_root_version_file_is_semver():
    version = _version_from_root()
    parts = version.split(".")
    assert len(parts) == 3, f"VERSION não é x.y.z: {version}"
    assert all(p.isdigit() for p in parts), f"VERSION tem parte não-numérica: {version}"


def test_settings_version_matches_root_version():
    assert settings.version == _version_from_root(), (
        "config.Settings.version divergiu da raiz/VERSION (fonte única da Fase 22)"
    )


def test_health_endpoint_uses_central_version():
    """O endpoint /api/health devolve a versão central (config.read root VERSION)."""
    from app.api.health import health

    import asyncio

    response = asyncio.run(health(user_agent=None))
    assert response.version == _version_from_root()
    assert response.version == settings.version


# ---------------------------------------------------------------------------
# Checksums de release (scripts/release_checksums.py)
# ---------------------------------------------------------------------------

def test_make_sums_hashes_and_sorts(tmp_path):
    module = _import_release_checksums()
    artifact = tmp_path / "VEGA-0.22.0-win-x64-portable.zip"
    artifact.write_bytes(b"A" * 64)
    other = tmp_path / "VEGA-0.22.0-android.apk"
    other.write_bytes(b"B" * 32)

    sums = module.make_sums("0.22.0", [artifact, other])

    lines = [ln for ln in sums.strip().splitlines() if ln]
    assert len(lines) == 2
    # ordenado por nome
    assert lines[0].endswith("VEGA-0.22.0-android.apk")
    assert lines[1].endswith("VEGA-0.22.0-win-x64-portable.zip")
    for ln in lines:
        digest, name = ln.split("  ")
        assert len(digest) == 64  # sha256 hex
        assert int(digest, 16) >= 0
        assert name == Path(name).name  # sem caminho, só nome


def test_make_sums_requires_artifacts_to_contain_version(tmp_path):
    module = _import_release_checksums()
    artifact = tmp_path / "app-debug.apk"
    artifact.write_bytes(b"x")
    try:
        module.make_sums("0.22.0", [artifact])
    except ValueError as exc:
        assert "0.22.0" in str(exc)
    else:
        raise AssertionError("esperava ValueError para artefato sem a versão no nome")


def test_make_sums_rejects_empty_and_missing(tmp_path):
    module = _import_release_checksums()
    empty = tmp_path / "VEGA-0.22.0-empty.bin"
    empty.write_bytes(b"")
    try:
        module.make_sums("0.22.0", [empty])
    except ValueError:
        pass
    else:
        raise AssertionError("esperava ValueError para arquivo vazio")

    missing = tmp_path / "VEGA-0.22.0-missing.bin"
    try:
        module.make_sums("0.22.0", [missing])
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("esperava FileNotFoundError para arquivo inexistente")


def test_sha256_of_matches_hashlib():
    module = _import_release_checksums()
    import hashlib

    import tempfile
    import os

    with tempfile.NamedTemporaryFile(delete=False) as fh:
        fh.write(b"vega content")
        name = fh.name
    try:
        assert module.sha256_of(Path(name)) == hashlib.sha256(b"vega content").hexdigest()
    finally:
        os.unlink(name)