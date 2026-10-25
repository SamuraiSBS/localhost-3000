"""Offline product tests and native design integrity; Docker is separate."""
import ast
import subprocess
import sys
from pathlib import Path
from check_design import check_design

root = Path(__file__).resolve().parents[1]
check_design(root)
for folder in ("app", "infra", "scripts", "tests"):
    for path in (root / folder).rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
subprocess.run([sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], cwd=root, check=True)
print("Python syntax and product tests passed. Docker/VPS/HTTPS are separate checks.")
