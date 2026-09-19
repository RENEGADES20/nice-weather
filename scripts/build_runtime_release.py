"""Build a production-only release from an exact Git commit, without workspace artifacts."""

import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
from pathlib import Path


def build(revision, output):
    sha = subprocess.check_output(["git", "rev-parse", revision + "^{commit}"], text=True).strip()
    paths = (
        subprocess.check_output(["git", "ls-tree", "-rz", "--name-only", sha]).decode().split("\0")
    )
    indexes = ["src/nice_weather/trading_chart_dist/index.html"]
    terminal = "src/nice_weather/terminal_dist/index.html"
    if terminal in paths:
        indexes.append(terminal)
    def read(path):
        return subprocess.check_output(["git", "show", f"{sha}:{path}"])
    assets = {
        str(Path(index).parent).replace("\\", "/") + "/" + p.removeprefix("./").lstrip("/")
        for index in indexes
        for p in re.findall(r'(?:src|href)="([^\"]+)"', read(index).decode())
        if p.startswith(("./", "/assets/"))
    }
    selected = (
        {
            p
            for p in paths
            if (
                p.startswith("src/nice_weather/")
                and p.endswith((".py", ".sql"))
                or p.startswith("config/")
                and (p.endswith(".toml") or p == "config/knyc-strategy-model.json")
            )
        }
        | assets
        | set(indexes)
        | {p for p in paths if p.startswith("deploy/systemd/nice-weather-knyc-")}
        | ({"deploy/systemd/nice-weather-terminal.service"} if terminal in paths else set())
        | {
            "pyproject.toml",
            "README.md",
            "deploy/journald-trading.conf",
            "deploy/systemd/nice-weather-sandbox.service",
            "deploy/systemd/nice-weather-backtest.service",
        }
    )
    contents = {p: read(p) for p in sorted(selected)}
    manifest = {
        "commit": sha,
        "files": {
            p: {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
            for p, body in contents.items()
        },
    }
    contents["runtime-manifest.json"] = json.dumps(manifest, indent=2).encode() + b"\n"
    with tarfile.open(output, "w:gz") as archive:
        for path, body in contents.items():
            info = tarfile.TarInfo(path)
            info.size, info.mode = len(body), 0o644
            archive.addfile(info, io.BytesIO(body))
    print(json.dumps({"commit": sha, "files": len(contents), "bytes": Path(output).stat().st_size}))


if __name__ == "__main__":
    build(*sys.argv[1:])
