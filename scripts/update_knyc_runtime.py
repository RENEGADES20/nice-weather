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
    if any(name in {"src/nice_weather/trading/feed.py", "src/nice_weather/trading/us_runtime.py"}
           for name in changed):
        # Both modules are imported by these existing KNYC workers. Keep legacy
        # KLGA collectors, dashboard and account processes running throughout.
        services += ["nice-weather-knyc-feed.service", "nice-weather-knyc-hrrr.service",
                     "nice-weather-knyc-backtest.service", "nice-weather-knyc-paper@kalshi.service",
                     "nice-weather-knyc-paper@poly_us.service"]
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
               "src/nice_weather/trading/us_runtime.py"}
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
    subprocess.run([py, "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check",
                    "PyJWT[crypto]>=2.10,<3"], check=True)
    subprocess.run(["systemctl", "stop", *services], check=True)
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
    for service in services:
        dropin = Path("/etc/systemd/system") / (service + ".d/release.conf")
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(
            "[Service]\nEnvironment=NICE_WEATHER_CODE_SHA=" + manifest["commit"] + "\n"
        )
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "start", *services], check=True)
    subprocess.run(["systemctl", "is-active", *services], check=True)


if __name__ == "__main__":
    main()
