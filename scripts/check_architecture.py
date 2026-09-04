from __future__ import annotations

import argparse
import re
from pathlib import Path

SECRET_FIELD = re.compile(
    r"(?i)(api[_-]?key|secret|password|authorization|cookie|token)"
)


def check_architecture(root: Path) -> list[str]:
    failures: list[str] = []
    schema_dir = root / "src" / "convictionos" / "api" / "schemas"
    if schema_dir.exists():
        for path in schema_dir.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if SECRET_FIELD.search(line):
                    failures.append(
                        f"{path.relative_to(root)}:{line_number}: "
                        "credential-like public schema field"
                    )

    main_py = root / "src" / "convictionos" / "api" / "main.py"
    if main_py.exists():
        text = main_py.read_text(encoding="utf-8")
        if "include_demo_routes=False" not in text:
            failures.append("production application must disable demo routes")

    for path in (root / "src" / "convictionos").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "alpaca_live_trade: bool = True" in text:
            failures.append(f"{path.relative_to(root)}: live trading flag defaults true")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    failures = check_architecture(args.root)
    if failures:
        raise SystemExit("\n".join(failures))
    print("architecture ok")


if __name__ == "__main__":
    main()
