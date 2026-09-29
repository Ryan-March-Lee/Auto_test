"""Compile project Python sources without writing bytecode files."""

from pathlib import Path
import sys


def main(root: Path) -> None:
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts or "test_results" in path.parts:
            continue
        compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
