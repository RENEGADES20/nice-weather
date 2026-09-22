"""Apply a verified terminal release to the existing VM runtime; no version copies.

Rollback uses an exact GitHub commit rebuilt by build_runtime_release.py. Business
databases are never opened by this updater. Existing obsolete files are listed for
approved cleanup, never deleted implicitly.
"""

import argparse
import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path, PurePosixPath


def affected_services(changed):
    services = ["nice-weather-terminal.service"]
    if any(name in {"src/nice_weather/trading/feed.py", "src/nice_weather/trading/storage.py"}
           for name in changed):
        services += ["nice-weather-knyc-feed.service", "nice-weather-knyc-hrrr.service"]
    elif any(name in {"src/nice_weather/trading/knyc_model.py", "config/knyc-strategy-model.json",
                     "src/nice_weather/trading/us_markets.py",
                     "src/nice_weather/trading/market_discovery.py"}
             for name in changed):
        services += ["nice-weather-knyc-feed.service"]
    if any(name in {"src/nice_weather/trading/feed.py", "src/nice_weather/trading/us_runtime.py",
                    "src/nice_weather/trading/us_fees.py", "src/nice_weather/trading/us_markets.py",
                    "src/nice_weather/trading/engine.py", "src/nice_weather/trading/signals_v2.py",
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
    parser.add_argument("--apply", action="store_true")
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
        "market_discovery", "market_weather", "signal_research", "paper_execution",
        "backtest_view", "us_live", "us_live_state", "us_reconcile")})
    allowed.add("deploy/systemd/nice-weather-knyc-live@.service")
    if any(name not in allowed and not name.startswith("src/nice_weather/terminal_dist/")
           for name in changed):
        raise ValueError("Release changes services outside the terminal update scope")
    obsolete = sorted(set(old["files"]) - set(manifest["files"]))
    services = affected_services(changed)
    print(json.dumps({"previous": old["commit"], "commit": manifest["commit"],
                      "changed": changed, "restart": services,
                      "obsolete_pending_review": obsolete}))
    if not args.apply:
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
        subprocess.run(["systemctl", "stop", *installed], check=True)
    for name in changed + ["runtime-manifest.json"]:
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
