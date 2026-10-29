# Hardware test boundary

Real-device tests belong here and are only started with
`tools/run_tests.py --layer hardware`. Each module calls
`hardware/smoke_gate.py` before opening a VISA resource. They must never be part
of ordinary `tests/` discovery: the directory has no `__init__.py`, and the
parallel plan excludes it explicitly.
