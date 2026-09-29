import importlib
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXCLUDED_MODULES = {"test_instrument_simulation"}


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite()
    for path in sorted((PROJECT_ROOT / "tests").glob("test_*.py")):
        if path.stem in EXCLUDED_MODULES:
            continue
        module = importlib.import_module(f"tests.{path.stem}")
        suite.addTests(loader.loadTestsFromModule(module))
    return suite


if __name__ == "__main__":
    unittest.main()
