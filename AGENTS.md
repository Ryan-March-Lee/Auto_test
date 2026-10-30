# Agent Execution Rules

## Python Environment

This project must use the following Python interpreter:

```text
D:\Anaconda\envs\Auto_test\python.exe
```

The Conda environment name is `Auto_test`. Do not use the Anaconda `base` environment, a bare `python` command, or a different interpreter path for project commands. Agent/tool processes may not inherit the user's activated Conda environment, so prefer the absolute interpreter path.

The interpreter must be the Python 3.11.x series. Dependencies come from `requirements.lock.txt` (pinned, including transitive packages); `requirements-dpd.txt` is DPD-only and must stay out of this environment. Details: `docs/依赖与运行时契约.md`. Do not install dependencies at runtime and do not run bare `pip install <package>` into this environment. Never hand-edit or `pip freeze >` the lock file: change dependencies, then regenerate it with `tools/export_dependency_lock.py` and run the full test suite.

## Commands

Run tests through the repository runner:

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py
```

The runner resolves the interpreter from `.env`, puts the Conda runtime directories on `PATH` (Qt and matplotlib DLLs load lazily), runs the in-memory compile check and the dependency-lock check, then the selected scope. The first failing step stops the run. A raw `-m unittest discover -s tests -v` skips the compile and dependency-lock checks, so prefer the runner unless you deliberately need a single module.

### Pick the test scope from the change surface

`tests/` is split so a pure UI change does not need the full suite. Choose by what you touched:

| Change surface | Command | What runs |
| --- | --- | --- |
| Only `presentation/qt` (widgets, layout, theme, page interaction) and no DTO/port signature change | `tools/run_tests.py --layer ui` | `tests/ui` (pure presentation) + all repository gates, ~40 s |
| `presentation` plus real wiring (new page bound to application services) | `tools/run_tests.py --layer integration` | `tests/ui` + `tests/integration` (real windows/pages with real backend) + gates, ~60 s |
| `application`, `domain`, `infrastructure`, `instrument`, `app`, `launcher.py`, `tools/` | `tools/run_tests.py` (full offline) | ~100 s |
| Dependencies, release, or anything before handing off | full suite + `launcher.py --check` + `launcher.py --validate-config` | — |

Rules:

- A pure UI task must **not** run the full suite; `tools/run_tests.py --layer ui` is the expected verification.
- Every scope still runs the repository gates (`tests/test_architecture_dependencies.py`, `test_presentation_boundaries.py`, `test_public_api_inventory.py`, `test_run_contract_inventory.py`, `test_exception_taxonomy.py`, `test_static_quality_gate.py`, `test_runtime_dependency_contract.py`); `--layer ui`/`integration` run them before their tests, and `tests/test_test_runner_contract.py` keeps that order.
- `tests/ui` may import only `presentation`, `application` DTO/ports and Qt; importing `app`/`infrastructure`/hardware fails `test_presentation_boundaries`. Real-backend UI scenarios belong in `tests/integration`.
- If the boundary is unclear, or a DTO/port signature changed, run the full suite. `tools/run_tests.py --parallel` runs the full suite with pytest-xdist (~1 min).

### Real-device smoke

`tests/hardware` drives real instruments; do not run it on a hunch and do not add it to ordinary discovery. Every module there calls `hardware/smoke_gate.py` before opening a VISA resource, and the gate refuses to start unless: the config is a local file under `hardware/` and not an example/template, it declares `environment: hardware_smoke` with `require_user_confirmation` and `require_empty_setup` both `true`, it contains no `REPLACE_WITH_` placeholder, `HARDWARE_SMOKE_ENABLED=1` is set, the process is not CI, and the operator confirmed the setup is unloaded:

```powershell
$env:HARDWARE_SMOKE_ENABLED = "1"
& "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py --layer hardware `
    --config hardware/smoke_config.local.json --confirm-hardware-smoke
```

Never set `HARDWARE_SMOKE_CONFIRM=1` yourself to make a run pass: that switch states the equipment is unloaded and the run is authorized.

Verify dependencies and the application with:

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" tools/export_dependency_lock.py --check
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --check
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --validate-config
```

Before running commands, verify that `D:\Anaconda\envs\Auto_test\python.exe` exists. If it does not exist, report the missing environment instead of trying `base` or installing dependencies into another environment.
