import importlib
import unittest
from unittest.mock import patch


class OptionalCapabilityBoundaryTests(unittest.TestCase):
    def test_csv_converter_has_no_import_time_file_access(self):
        with patch("pathlib.Path.open", side_effect=AssertionError("import accessed a file")):
            module = importlib.import_module("format_convert.csv_to_MDIF")
        self.assertTrue(callable(module.convert_csv_to_mdif))


if __name__ == "__main__":
    unittest.main()
