"""Offline checks; the probe itself has no third-party dependencies."""
import ast
import subprocess
import sys
from pathlib import Path
from check_design import check_design

root = Path(__file__).resolve().parents[1]
check_design(root)
for folder in ("infra", "scripts", "tests"):
    for path in (root / folder).rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root, check=True)
print("Python syntax and HTTP probe tests passed. Docker/VPS/HTTPS are separate checks.")
