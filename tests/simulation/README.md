# Simulation test boundary

New simulated-device integration tests should use this directory and the
`test_*.py` naming convention. The current simulation suite remains in the
legacy flat location until a later migration step. During this transition,
`run_tests.ps1 -Layer Simulation` runs the legacy simulation module explicitly
so it remains independent from the default offline discovery.
