import hashlib
import importlib.util
import io
import json
import os
import stat
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


@pytest.fixture
def root_metadata(monkeypatch):
    # The updater targets root-owned Linux paths; tests also run as non-root/on Windows.
    original = os.lstat
    modes = {}

    def owned(path, *args, **kwargs):
        info = list(original(path, *args, **kwargs))
        info[0], info[4] = modes.get(str(path), info[0] & ~0o022), 0
        return os.stat_result(info)

    monkeypatch.setattr(Path, "lstat", owned)
    return modes


@pytest.mark.parametrize("module,fail,prepare_only", [
    ("feed", None, False), ("market_weather", None, False), ("api", None, False),
    ("feed", "online", False), ("feed", "final", False), ("feed", None, True)])
def test_projection_runs_after_install_before_start(
        tmp_path, monkeypatch, root_metadata, module, fail, prepare_only):
    runtime = tmp_path / "opt/nice-weather/knyc-current"
    name = f"src/nice_weather/trading/{module}.py"
    original = {key: b"# old runtime" for key in (*UPDATER.PREPARATION_FILES, name)}
    for key, body in original.items():
        target = runtime / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    target = runtime / name
    content = original | {name: b"# new runtime"}
    def specs(rows):
        return {key: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
                for key, body in rows.items()}

    old = {"commit": "old", "files": specs(original)}
    manifest = {"commit": "new", "files": specs(content)}
    (runtime / "runtime-manifest.json").write_text(json.dumps(old))
    content["runtime-manifest.json"] = json.dumps(manifest).encode()
    monkeypatch.setattr(UPDATER, "read_release", lambda *args: (manifest, content))
    monkeypatch.setattr(UPDATER, "Path", lambda value: tmp_path / str(value).lstrip("/"))
    monkeypatch.setattr(UPDATER.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(UPDATER.argparse.ArgumentParser, "parse_args", lambda self:
                        SimpleNamespace(archive=None, archive_sha256="a" * 64,
                                        apply=not prepare_only, prepare_only=prepare_only))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "runuser":
            source = kwargs["cwd"]
            phase = "final" if source == runtime else "online"
            assert target.read_bytes() == (content[name] if phase == "final" else original[name])
            assert json.loads((runtime / "runtime-manifest.json").read_text())["commit"] == "old"
            assert command[:7] == ["runuser", "-u", "nice-weather", "--", "env",
                                   "PYTHONPATH=" + str(source / "src"), "PYTHONUNBUFFERED=1"]
            assert command[7:10] == [str(runtime / ".venv/bin/python"), "-B", "-c"]
            assert command[-2:] == ["/var/lib/nice-weather-knyc/feed.sqlite3", str(source / "src")]
            assert kwargs["check"] is True
            assert any(c[:2] == ["systemctl", "stop"] for c in calls) == (phase == "final")
            assert not any(c[:2] == ["systemctl", "start"] for c in calls)
            if fail == phase:
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
        if fail == "online":
            assert not any(c[0] == "systemctl" for c in calls)
            assert target.read_bytes() == original[name]
    else:
        UPDATER.main()
        if prepare_only:
            assert not any(c[0] == "systemctl" for c in calls)
            assert json.loads((runtime / "runtime-manifest.json").read_text()) == old
            assert all((runtime / key).read_bytes() == body for key, body in original.items())
        else:
            assert any(c[:2] == ["systemctl", "start"] for c in calls)
    expected = 0 if module == "api" else 1 if prepare_only or fail == "online" else 2
    assert sum(c[0] == "runuser" for c in calls) == expected


@pytest.mark.parametrize("pollution", ["bytes", "extra", "symlink", "writable", "unreadable"])
def test_preparation_rejects_staging_pollution(tmp_path, monkeypatch, root_metadata, pollution):
    monkeypatch.setattr(UPDATER, "Path", lambda value: tmp_path / str(value).lstrip("/"))
    content = {name: b"# verified source" for name in UPDATER.PREPARATION_FILES}
    manifest = {"commit": "verified", "files": {name: {
        "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        for name, body in content.items()}}
    stage = UPDATER.stage_preparation(manifest, content, "b" * 64)
    assert UPDATER.stage_preparation(manifest, content, "b" * 64) == stage
    target = stage / "src/nice_weather/trading/feed.py"
    if pollution == "bytes":
        target.write_bytes(b"# another release")
    elif pollution == "extra":
        (stage / "src/nice_weather/injected.py").write_bytes(b"# unexpected module")
    else:
        root_metadata[str(target)] = ((stat.S_IFLNK | 0o777) if pollution == "symlink"
                                      else (stat.S_IFREG | (0o600 if pollution == "unreadable"
                                                            else 0o666)))
    with pytest.raises(ValueError, match="[Pp]reparation"):
        UPDATER.stage_preparation(manifest, content, "b" * 64)


def test_projection_skips_old_runtime_without_prepare(monkeypatch, capsys):
    # A rollback must not touch its database through a newer projection routine.
    legacy = ModuleType("nice_weather.trading.market_weather")
    import nice_weather.trading

    monkeypatch.setitem(sys.modules, "nice_weather.trading.market_weather", legacy)
    monkeypatch.setattr(nice_weather.trading, "market_weather", legacy, raising=False)
    exec(UPDATER.WEATHER_PROJECTION, {})
    assert json.loads(capsys.readouterr().out) == {
        "weather_projection": "skipped", "reason": "legacy runtime"}



@pytest.mark.parametrize("probability", ["ready", "absent", "failed"])
def test_projection_initializes_current_runtime_and_reports_counts(
        tmp_path, monkeypatch, capsys, probability):
    from nice_weather.trading import feed, market_weather

    # Exercise the child program against SQLite, including optional preparation order.
    path = tmp_path / "feed.sqlite3"
    path.touch()
    monkeypatch.setattr(sys, "argv", ["-c", str(path),
                                    str(Path(__file__).resolve().parents[2] / "src")])
    calls = []
    store, weather = feed.FeedStore, market_weather.prepare_weather_history

    def initialize(db):
        calls.append("init")
        return store(db)

    def prepare_probability(db):
        assert db == path
        calls.append("probability")
        if probability == "failed":
            raise RuntimeError("probability projection failed")

    def prepare_weather(db):
        calls.append("weather")
        return weather(db)

    monkeypatch.setattr(feed, "FeedStore", initialize)
    monkeypatch.setattr(market_weather, "prepare_weather_history", prepare_weather)
    if probability == "absent":
        monkeypatch.delattr(feed, "prepare_probability_history", raising=False)
    else:
        monkeypatch.setattr(feed, "prepare_probability_history", prepare_probability, raising=False)
    if probability == "failed":
        with pytest.raises(RuntimeError, match="probability projection failed"):
            exec(UPDATER.WEATHER_PROJECTION, {})
        assert calls == ["init", "probability"]
    else:
        exec(UPDATER.WEATHER_PROJECTION, {})
        assert calls == (["init", "weather"] if probability == "absent"
                         else ["init", "probability", "weather"])
        report = json.loads(capsys.readouterr().out)
        assert report["weather_projection"] == "ready"
        assert report["points"] == report["cli_reports"] == report["through_seq"] == 0
        assert report["seconds"] >= 0



def test_staging_modes_ignore_umask_without_changing_existing_paths(
        tmp_path, monkeypatch, root_metadata):
    monkeypatch.setattr(UPDATER, "Path", lambda value: tmp_path / str(value).lstrip("/"))
    content = {name: b"# verified source" for name in UPDATER.PREPARATION_FILES}
    manifest = {"commit": "verified", "files": {name: {
        "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        for name, body in content.items()}}
    original = os.chmod
    calls = []

    def chmod(path, mode, *args, **kwargs):
        assert Path(path).is_relative_to(tmp_path / "opt")
        calls.append((Path(path), mode))
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(UPDATER.os, "chmod", chmod)
    previous = os.umask(0o077)
    try:
        stage = UPDATER.stage_preparation(manifest, content, "c" * 64)
    finally:
        os.umask(previous)
    assert (stage, 0o755) in calls
    assert (stage / "src/nice_weather/trading/feed.py", 0o644) in calls
    calls.clear()
    UPDATER.stage_preparation(manifest, content, "c" * 64)
    assert calls == []
