"""Apply a verified terminal release to the existing VM runtime; no version copies.

Rollback uses an exact GitHub commit rebuilt by build_runtime_release.py. Weather
projections are prepared online from retained verified source, then caught up before restart.
Existing obsolete files are listed for approved cleanup, never deleted implicitly.
"""

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tarfile
import time
from pathlib import Path, PurePosixPath


def affected_services(changed):
    services = ["nice-weather-terminal.service"]
    if any(name in {"src/nice_weather/trading/feed.py", "src/nice_weather/trading/storage.py",
                    "src/nice_weather/trading/market_weather.py"}
           for name in changed):
        services += ["nice-weather-knyc-feed.service", "nice-weather-knyc-hrrr.service"]
    elif any(name in {"src/nice_weather/trading/knyc_model.py", "config/knyc-strategy-model.json",
                     "src/nice_weather/trading/us_markets.py",
                     "src/nice_weather/trading/market_discovery.py"}
             for name in changed):
        services += ["nice-weather-knyc-feed.service"]
    if any(name in {"src/nice_weather/trading/feed.py",
                    "src/nice_weather/trading/market_weather.py",
                    "src/nice_weather/trading/us_runtime.py",
                    "src/nice_weather/trading/us_fees.py", "src/nice_weather/trading/us_markets.py",
                    "src/nice_weather/trading/engine.py", "src/nice_weather/trading/signals.py",
                    "src/nice_weather/trading/signals_v2.py",
                    "src/nice_weather/trading/recovery.py", "src/nice_weather/trading/storage.py",
                    "src/nice_weather/trading/paper_execution.py"}
           for name in changed):
        # us_runtime is used only by Paper and replay. Terminal restarts to expose
        # the current release identity; collectors continue for Paper-only changes.
        services += ["nice-weather-knyc-backtest.service", "nice-weather-knyc-paper@kalshi.service",
                     "nice-weather-knyc-paper@poly_us.service"]
    if "src/nice_weather/trading/backtest_view.py" in changed:
        services += ["nice-weather-knyc-backtest.service"]
    if any(name in {f"src/nice_weather/trading/{module}.py" for module in (
            "storage", "credentials", "live_budget", "us_live", "us_live_state",
            "us_execution", "us_transport", "us_reports", "us_currency", "us_reconcile",
            "us_markets", "us_fees")} or name ==
            "deploy/systemd/nice-weather-knyc-live@.service" for name in changed):
        services += ["nice-weather-knyc-live@kalshi.service",
                     "nice-weather-knyc-live@poly_us.service"]
    services = list(dict.fromkeys(services))
    return services


WEATHER_PROJECTION = """
import json
import sys
import time
from pathlib import Path
from nice_weather.trading import market_weather

prepare = getattr(market_weather, "prepare_weather_history", None)
if prepare is None:
    print(json.dumps({"weather_projection": "skipped", "reason": "legacy runtime"}), flush=True)
else:
    import importlib
    from nice_weather.trading import feed
    from nice_weather.trading.storage import connect

    source = Path(sys.argv[2]).resolve()
    for name in ("nice_weather", "nice_weather.trading", "nice_weather.trading.feed",
                 "nice_weather.trading.market_weather", "nice_weather.trading.storage",
                 "nice_weather.trading.us_markets"):
        module = importlib.import_module(name)
        if not Path(module.__file__).resolve().is_relative_to(source):
            raise ValueError("Preparation imported a different release: " + name)
    path = Path(sys.argv[1])
    if not path.is_file():
        raise FileNotFoundError(path)
    started = time.monotonic()
    feed.FeedStore(path)
    prepare_probability = getattr(feed, "prepare_probability_history", None)
    if prepare_probability is not None:
        prepare_probability(path)
    prepare(path)
    with connect(path, readonly=True) as con:
        points = con.execute("SELECT COUNT(*) FROM weather_chart_points").fetchone()[0]
        reports = con.execute("SELECT COUNT(*) FROM weather_chart_cli").fetchone()[0]
        seq = con.execute("SELECT seq FROM weather_chart_progress WHERE id=1").fetchone()[0]
    print(json.dumps({"weather_projection": "ready",
                      "seconds": round(time.monotonic() - started, 3), "points": points,
                      "cli_reports": reports, "through_seq": seq}), flush=True)
"""


PROJECTION_MODULES = {"src/nice_weather/trading/feed.py",
                      "src/nice_weather/trading/market_weather.py"}
PREPARATION_FILES = ("src/nice_weather/__init__.py", "src/nice_weather/trading/__init__.py",
                     "src/nice_weather/trading/feed.py",
                     "src/nice_weather/trading/market_weather.py",
                     "src/nice_weather/trading/storage.py",
                     "src/nice_weather/trading/us_markets.py")


def trusted_staging_path(path, *, directory=False):
    info = path.lstat()
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    readable = 0o005 if directory else 0o004
    if (not correct_type or info.st_uid != 0 or info.st_mode & 0o022
            or info.st_mode & readable != readable):
        raise ValueError("Untrusted preparation path: " + str(path))


def staging_directory(path):
    if path.parent != path:
        staging_directory(path.parent)
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o755)
        path.chmod(0o755)  # Only newly created staging directories; ignore root umask.
    trusted_staging_path(path, directory=True)


def stage_preparation(manifest, content, archive_hash):
    if len(archive_hash) != 64 or any(c not in "0123456789abcdef" for c in archive_hash):
        raise ValueError("Invalid preparation archive hash")
    root = Path("/opt/nice-weather-knyc-prepare") / archive_hash
    staging_directory(root)
    if any(name not in manifest["files"] or name not in content for name in PREPARATION_FILES):
        raise ValueError("Release lacks preparation modules")
    files = {name: content[name] for name in PREPARATION_FILES}
    identity = {"commit": manifest["commit"], "archive_sha256": archive_hash,
                "files": {name: manifest["files"][name] for name in PREPARATION_FILES}}
    files["preparation-manifest.json"] = json.dumps(identity, sort_keys=True).encode()
    directories = {parent.as_posix() for name in files for parent in PurePosixPath(name).parents
                   if parent != PurePosixPath(".")}
    # Reuse only this exact verified source set; stale or injected code fails closed.
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if name not in files and name not in directories:
            raise ValueError("Unexpected preparation file: " + name)
        trusted_staging_path(path, directory=name in directories)
    for name, body in files.items():
        target = root / name
        staging_directory(target.parent)
        if target.exists() or target.is_symlink():
            trusted_staging_path(target)
            if target.read_bytes() != body:
                raise ValueError("Preparation content differs: " + name)
        else:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            target.chmod(0o644)  # Publish only this newly created, complete source file.
            trusted_staging_path(target)
    return root


def prepare_weather_projection(runtime, changed, *, source=None, phase="final", commit=None):
    if PROJECTION_MODULES.intersection(changed):
        source = source or runtime
        print(json.dumps({"prepare": phase, "state": "started", "commit": commit,
                          "source": str(source)}), flush=True)
        subprocess.run(["runuser", "-u", "nice-weather", "--", "env",
                        "PYTHONPATH=" + str(source / "src"), "PYTHONUNBUFFERED=1",
                        str(runtime / ".venv/bin/python"), "-B", "-c", WEATHER_PROJECTION,
                        "/var/lib/nice-weather-knyc/feed.sqlite3", str(source / "src")],
                       cwd=source, check=True)
        print(json.dumps({"prepare": phase, "state": "completed", "commit": commit}), flush=True)


def read_release(archive_path, expected_hash):
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("Archive hash mismatch")
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or any(
            not m.isfile() or PurePosixPath(m.name).is_absolute()
            or ".." in PurePosixPath(m.name).parts or "\\" in m.name
            or m.size > 20_000_000 for m in members
        ):
            raise ValueError("Invalid release paths or member sizes")
        content = {m.name: archive.extractfile(m).read() for m in members}
    manifest = json.loads(content["runtime-manifest.json"])
    if set(content) != set(manifest["files"]) | {"runtime-manifest.json"}:
        raise ValueError("Unexpected release contents")
    for name, spec in manifest["files"].items():
        if len(content[name]) != spec["bytes"] or (
            hashlib.sha256(content[name]).hexdigest() != spec["sha256"]
        ):
            raise ValueError("Manifest mismatch: " + name)
    return manifest, content


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("archive_sha256")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    manifest, content = read_release(args.archive, args.archive_sha256)
    runtime = Path("/opt/nice-weather/knyc-current").resolve(strict=True)
    runtime.relative_to(Path("/opt/nice-weather"))
    old = json.loads((runtime / "runtime-manifest.json").read_text())
    changed = [name for name in manifest["files"] if
               old["files"].get(name) != manifest["files"][name]]
    # Only the terminal and reviewed capture/Paper modules are allowed here.
    # Other changes need a separately reviewed service-impact plan.
    allowed = {"pyproject.toml", "README.md", "src/nice_weather/trading/api.py",
               "src/nice_weather/trading/access.py", "src/nice_weather/trading/feed.py",
               "src/nice_weather/trading/us_runtime.py", "src/nice_weather/trading/engine.py",
               "src/nice_weather/trading/signals_v2.py", "src/nice_weather/trading/recovery.py",
               "src/nice_weather/trading/credentials.py", "src/nice_weather/trading/live_budget.py",
               "src/nice_weather/trading/us_transport.py", "src/nice_weather/trading/knyc_model.py",
               "config/knyc-strategy-model.json"}
    allowed.update({"src/nice_weather/trading/us_fees.py",
                    "src/nice_weather/trading/us_markets.py",
                    "src/nice_weather/trading/us_reports.py",
                    "src/nice_weather/trading/us_execution.py",
                    "src/nice_weather/trading/us_currency.py",
                    "src/nice_weather/trading/storage.py",
                    "src/nice_weather/store.py"})
    allowed.update({f"src/nice_weather/trading/{module}.py" for module in (
        "market_discovery", "market_weather", "signals", "signal_research", "paper_execution",
        "backtest_view", "us_live", "us_live_state", "us_reconcile")})
    allowed.add("deploy/systemd/nice-weather-knyc-live@.service")
    # R2 retention is installed and scheduled independently; package its merged
    # runtime files without restarting the collector or running a prune here.
    allowed.update({"src/nice_weather/r2_archive.py",
                    "src/nice_weather/trading/r2_retention.py",
                    "deploy/systemd/nice-weather-knyc-r2.service",
                    "deploy/systemd/nice-weather-knyc-r2.timer"})
    if any(name not in allowed and not name.startswith("src/nice_weather/terminal_dist/")
           for name in changed):
        raise ValueError("Release changes services outside the terminal update scope")
    obsolete = sorted(set(old["files"]) - set(manifest["files"]))
    services = affected_services(changed)
    print(json.dumps({"previous": old["commit"], "commit": manifest["commit"],
                      "changed": changed, "restart": services,
                      "obsolete_pending_review": obsolete}))
    if not (args.apply or args.prepare_only):
        return
    if os.geteuid() != 0:
        raise PermissionError("Run with sudo")
    # Verify the deployed base before overwriting code; reject unexpected edits.
    for name, spec in old["files"].items():
        hashes = {spec["sha256"]}
        if name in changed:
            hashes.add(manifest["files"][name]["sha256"])
        if hashlib.sha256((runtime / name).read_bytes()).hexdigest() not in hashes:
            raise ValueError("Deployed base differs: " + name)
    py = str(runtime / ".venv/bin/python")
    subprocess.run([py, "-c", "import jwt, cryptography, nautilus_trader"], check=True)
    if PROJECTION_MODULES.intersection(changed):
        source = stage_preparation(manifest, content, args.archive_sha256)
        prepare_weather_projection(runtime, changed, source=source, phase="online",
                                   commit=manifest["commit"])
    if args.prepare_only:
        return
    live = [s for s in services if s.startswith("nice-weather-knyc-live@")]
    for service in live:
        venue = service.split("@", 1)[1].removesuffix(".service")
        if not Path(f"/etc/nice-weather/knyc-live-{venue}.env").is_file():
            raise ValueError(f"Missing Live environment file for {venue}")
    # New Live units are installed below; stop only units already present.
    installed = [s for s in services if subprocess.run(
        ["systemctl", "cat", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    ).returncode == 0]
    if installed:
        started = time.monotonic()
        print(json.dumps({"stop": "started", "services": installed}), flush=True)
        subprocess.run(["systemctl", "stop", *installed], check=True)
        print(json.dumps({"stop": "completed", "seconds": round(time.monotonic() - started, 3)}),
              flush=True)
    for name in changed + ["runtime-manifest.json"]:
        if name == "runtime-manifest.json":
            # Commit the manifest only after preparation succeeds, so failures can retry.
            prepare_weather_projection(runtime, changed, commit=manifest["commit"])
        target = runtime / name
        if not target.resolve().is_relative_to(runtime):
            raise ValueError("Target escapes runtime")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".installing")
        if temporary.is_symlink():
            raise ValueError("Unexpected temporary symlink")
        with temporary.open("wb") as stream:
            stream.write(content[name])
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    unit = "deploy/systemd/nice-weather-knyc-live@.service"
    if unit in changed:
        Path("/etc/systemd/system/nice-weather-knyc-live@.service").write_bytes(content[unit])
    for service in services:
        dropin = Path("/etc/systemd/system") / (service + ".d/release.conf")
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(
            "[Service]\nEnvironment=NICE_WEATHER_CODE_SHA=" + manifest["commit"] + "\n"
        )
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    if live:
        subprocess.run(["systemctl", "enable", *live], check=True)
    subprocess.run(["systemctl", "start", *services], check=True)
    subprocess.run(["systemctl", "is-active", *services], check=True)


if __name__ == "__main__":
    main()
