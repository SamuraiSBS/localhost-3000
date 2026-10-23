"""Add PyPI release SHA256 hashes to exact pip-compile pins, without wheel downloads.

Usage: python scripts/lock_hashes.py requirements.txt requirements-dev.txt
Run pip-compile first; actual installation must still use --require-hashes.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen

PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9_.!+~-]+)\s*\\?$")
HASH = re.compile(r"--hash=sha256:[a-f0-9]{64}\s*\\?$")


def read_pins(path: Path) -> list[tuple[str, str]]:
    pins = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or HASH.fullmatch(line):
            continue
        match = PIN.fullmatch(line)
        if not match:
            raise ValueError(f"{path}: expected exact package==version, got {line!r}")
        pin = match.groups()
        if pin in pins:
            raise ValueError(f"{path}: duplicate pin {pin[0]}")
        pins.append(pin)
    if not pins:
        raise ValueError(f"{path}: no pins")
    return pins


def release_hashes(pin: tuple[str, str]) -> tuple[tuple[str, str], list[str]]:
    name, version = pin
    request = Request(f"https://pypi.org/pypi/{name}/{version}/json",
                      headers={"User-Agent": "localhost3000-lock/1"})
    with urlopen(request, timeout=30) as response:
        release = json.load(response)
    if release["info"]["version"] != version:
        raise ValueError(f"{name}: unexpected metadata version")
    hashes = sorted({artifact["digests"]["sha256"] for artifact in release["urls"]
                     if not artifact.get("yanked", False)})
    if not hashes or not all(re.fullmatch(r"[a-f0-9]{64}", value) for value in hashes):
        raise ValueError(f"{name}=={version}: missing/invalid non-yanked artifact hashes")
    return pin, hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    files = {path: read_pins(path) for path in args.files}
    pins = sorted({pin for values in files.values() for pin in values})
    with ThreadPoolExecutor(max_workers=8) as executor:
        hashes = dict(executor.map(release_hashes, pins))
    # Fetch/validate every release before writing anything. Temporary files stay
    # inside the task-owned ignored environment, then replace on the same drive.
    temporary = Path(__file__).resolve().parents[1] / ".venv" / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    for path, values in files.items():
        lines = ["# Exact pins resolved by pip-compile on Python 3.13.",
                 "# SHA256: official https://pypi.org/pypi/<name>/<version>/json",
                 "# Enriched by scripts/lock_hashes.py; install with --require-hashes.", ""]
        for name, version in values:
            lines.append(f"{name}=={version} \\")
            artifacts = hashes[(name, version)]
            for index, digest in enumerate(artifacts):
                continuation = " \\" if index < len(artifacts) - 1 else ""
                lines.append(f"    --hash=sha256:{digest}{continuation}")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=temporary, delete=False) as stream:
            stream.write("\n".join(lines) + "\n")
            staged = Path(stream.name)
        try:
            os.replace(staged, path)
        finally:
            staged.unlink(missing_ok=True)
        print(f"{path}: {len(values)} exact pins, release artifact SHA256 recorded")


if __name__ == "__main__":
    main()
