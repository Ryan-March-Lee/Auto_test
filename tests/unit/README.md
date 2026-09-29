# Unit test boundary

Existing flat tests remain in `tests/` during the migration so the legacy
discovery entry point stays compatible. New offline unit tests should use this
directory and the `test_*.py` naming convention. The dedicated layer command
is `run_tests.ps1 -Layer Unit`.
