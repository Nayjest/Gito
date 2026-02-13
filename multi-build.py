"""
Multi-build script for delivering to PYPI with aliased names.

(c) Vitalii Stepanenko (Nayjest) <mailto:mail@vitaliy.in>, 2025
"""

import re
from pathlib import Path
import subprocess

NAMES = [
    ["gito.bot"],
    ["ai-code-review"],
    ["ai-cr"],
    ["github-code-review"],
]
FILES = [
    "pyproject.toml",
]


def replace_name(old_names: list[str], new_names: list[str], files: list[str] = None):
    files = files or FILES
    for i in range(len(old_names)):
        old_name = old_names[i]
        new_name = new_names[i]
        for path in files:
            p = Path(path)
            p.write_text(
                re.sub(
                    rf"(?<![\\/\w\-\_]){old_name}\b",
                    new_name,
                    p.read_text(encoding="utf-8"),
                    flags=re.M,
                ),
                encoding="utf-8",
            )


prev = NAMES[0]
for nxt in NAMES[1:] + [NAMES[0]]:
    print(f"Building for project name: {nxt[0]}...")
    replace_name(prev, nxt)
    subprocess.run(["poetry", "build"], check=True)
    prev = nxt
print("All builds completed.")
