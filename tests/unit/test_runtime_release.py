import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "runtime_updater", Path(__file__).resolve().parents[2] / "scripts/update_knyc_runtime.py"
)
UPDATER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATER)


def test_service_impact_preserves_legacy_processes():
    assert UPDATER.affected_services(["src/nice_weather/trading/api.py"]) == [
        "nice-weather-terminal.service"
    ]
    services = UPDATER.affected_services(["src/nice_weather/trading/feed.py"])
    assert len(services) == 6
    assert "nice-weather-knyc-paper@kalshi.service" in services
    assert "nice-weather-collector.service" not in services
    assert "nice-weather-sandbox.service" not in services


def release(tmp_path, *, name="src/nice_weather/trading/api.py", duplicate=False):
    body = b"print('fixture')\n"
    manifest = {"commit": "a" * 40, "files": {name: {
        "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
    }}}
    path = tmp_path / "release.tar.gz"
    members = [(name, body), ("runtime-manifest.json", json.dumps(manifest).encode())]
    if duplicate:
        members.append((name, body))
    with tarfile.open(path, "w:gz") as archive:
        for member_name, data in members:
            member = tarfile.TarInfo(member_name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_verified_runtime_archive_and_tampering(tmp_path):
    path, checksum = release(tmp_path)
    manifest, content = UPDATER.read_release(path, checksum)
    assert manifest["commit"] == "a" * 40
    assert len(content) == 2
    with pytest.raises(ValueError, match="hash mismatch"):
        UPDATER.read_release(path, "0" * 64)


@pytest.mark.parametrize("name,duplicate", [
    ("../../outside", False), ("/etc/passwd", False),
    ("src/nice_weather/trading/api.py", True),
])
def test_unsafe_or_duplicate_archive_members_rejected(tmp_path, name, duplicate):
    path, checksum = release(tmp_path, name=name, duplicate=duplicate)
    with pytest.raises(ValueError, match="Invalid release"):
        UPDATER.read_release(path, checksum)
