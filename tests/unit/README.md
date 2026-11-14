# Unit test boundary

Offline unit tests live in this directory and use the `test_*.py` naming
convention. The dedicated layer command is `tools/run_tests.py --layer unit`.
Pure UI, integration and hardware tests belong in their own directories so this
layer stays fast and does not re-run the flat offline suite.
