import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

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
    assert UPDATER.affected_services(["src/nice_weather/trading/market_weather.py"]) == services
    assert "nice-weather-knyc-paper@kalshi.service" in services
    assert "nice-weather-collector.service" not in services
    assert "nice-weather-sandbox.service" not in services
    assert UPDATER.affected_services(["src/nice_weather/trading/storage.py"]) == services + [
        "nice-weather-knyc-live@kalshi.service", "nice-weather-knyc-live@poly_us.service"]
    # The legacy store is packaged here but no KNYC worker imports it. Updating
    # this copy does not deploy to the separate legacy checkout or restart it.
    assert UPDATER.affected_services(["src/nice_weather/store.py"]) == [
        "nice-weather-terminal.service"
    ]
    paper_services = UPDATER.affected_services(["src/nice_weather/trading/us_runtime.py"])
    assert len(paper_services) == 4
    assert "nice-weather-knyc-feed.service" not in paper_services
    assert "nice-weather-knyc-hrrr.service" not in paper_services
    assert "nice-weather-knyc-paper@kalshi.service" in paper_services
    assert UPDATER.affected_services(["src/nice_weather/trading/engine.py"]) == paper_services
    assert UPDATER.affected_services(["src/nice_weather/trading/signals.py"]) == paper_services
    assert UPDATER.affected_services(["config/knyc-strategy-model.json"]) == [
        "nice-weather-terminal.service", "nice-weather-knyc-feed.service"
    ]


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


@pytest.mark.parametrize("module,fail", [("feed", False), ("market_weather", False),
                                        ("api", False), ("feed", True)])
def test_projection_runs_after_install_before_start(tmp_path, monkeypatch, module, fail):
    runtime = tmp_path / "opt/nice-weather/knyc-current"
    name = f"src/nice_weather/trading/{module}.py"
    target = runtime / name
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old runtime")
    old = {"commit": "old", "files": {name: {
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "bytes": target.stat().st_size}}}
    (runtime / "runtime-manifest.json").write_text(json.dumps(old))
    content = {name: b"new runtime"}
    manifest = {"commit": "new", "files": {name: {
        "sha256": hashlib.sha256(content[name]).hexdigest(), "bytes": len(content[name])}}}
    content["runtime-manifest.json"] = json.dumps(manifest).encode()
    monkeypatch.setattr(UPDATER, "read_release", lambda *args: (manifest, content))
    monkeypatch.setattr(UPDATER, "Path", lambda value: tmp_path / str(value).lstrip("/"))
    monkeypatch.setattr(UPDATER.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(UPDATER.argparse.ArgumentParser, "parse_args", lambda self:
                        SimpleNamespace(archive=None, archive_sha256=None, apply=True))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "runuser":
            assert target.read_bytes() == content[name]
            assert json.loads((runtime / "runtime-manifest.json").read_text())["commit"] == "old"
            assert command[:7] == ["runuser", "-u", "nice-weather", "--", "env",
                                   "PYTHONPATH=" + str(runtime / "src"), "PYTHONUNBUFFERED=1"]
            assert command[7] == str(runtime / ".venv/bin/python")
            assert command[-1] == "/var/lib/nice-weather-knyc/feed.sqlite3"
            assert kwargs == {"cwd": runtime, "check": True}
            assert any(c[:2] == ["systemctl", "stop"] for c in calls)
            assert not any(c[:2] == ["systemctl", "start"] for c in calls)
            if fail:
                raise subprocess.CalledProcessError(1, command)
        if command[:2] == ["systemctl", "start"]:
            assert json.loads((runtime / "runtime-manifest.json").read_text())["commit"] == "new"
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(UPDATER.subprocess, "run", run)
    if fail:
        with pytest.raises(subprocess.CalledProcessError):
            UPDATER.main()
        assert not any(c[:2] == ["systemctl", "start"] for c in calls)
        assert json.loads((runtime / "runtime-manifest.json").read_text()) == old
    else:
        UPDATER.main()
        assert any(c[:2] == ["systemctl", "start"] for c in calls)
    assert sum(c[0] == "runuser" for c in calls) == (module != "api")


def test_projection_skips_old_runtime_without_prepare(monkeypatch, capsys):
    # A rollback must not touch its database through a newer projection routine.
    legacy = ModuleType("nice_weather.trading.market_weather")
    import nice_weather.trading

    monkeypatch.setitem(sys.modules, "nice_weather.trading.market_weather", legacy)
    monkeypatch.setattr(nice_weather.trading, "market_weather", legacy, raising=False)
    exec(UPDATER.WEATHER_PROJECTION, {})
    assert json.loads(capsys.readouterr().out) == {
        "weather_projection": "skipped", "reason": "legacy runtime"}



def test_projection_initializes_current_runtime_and_reports_counts(tmp_path, monkeypatch, capsys):
    # Exercise the child program against a real empty SQLite file, without runuser/systemd.
    path = tmp_path / "feed.sqlite3"
    path.touch()
    monkeypatch.setattr(sys, "argv", ["-c", str(path)])
    exec(UPDATER.WEATHER_PROJECTION, {})
    report = json.loads(capsys.readouterr().out)
    assert report["weather_projection"] == "ready"
    assert report["points"] == report["cli_reports"] == report["through_seq"] == 0
    assert report["seconds"] >= 0
