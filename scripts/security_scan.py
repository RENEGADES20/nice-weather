from __future__ import annotations

import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "Cloudflare token assignment": re.compile(
        r"(?i)\b(?:CLOUDFLARE_API_TOKEN|R2_SECRET_ACCESS_KEY)\s*=\s*['\"]?[A-Za-z0-9/+_-]{20,}"
    ),
}


def repository_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    return [Path(item.decode()) for item in output.split(b"\0") if item]


def main() -> int:
    findings = []
    for path in repository_files():
        if path.as_posix() == "scripts/security_scan.py" or path.parts[:1] == ("tmp",):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{path}:{line_number}: possible {name}")
    if findings:
        print("\n".join(findings))
        return 1
    print("No high-confidence secrets detected in tracked text files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
