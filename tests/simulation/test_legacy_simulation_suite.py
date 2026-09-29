import unittest


def load_tests(loader, tests, pattern):
    return loader.loadTestsFromName("tests.test_instrument_simulation")


if __name__ == "__main__":
    unittest.main()
