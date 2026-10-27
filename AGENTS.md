# Agent Execution Rules

## Python Environment

This project must use the following Python interpreter:

```text
D:\Anaconda\envs\Auto_test\python.exe
```

The Conda environment name is `Auto_test`. Do not use the Anaconda `base` environment, a bare `python` command, or a different interpreter path for project commands. Agent/tool processes may not inherit the user's activated Conda environment, so prefer the absolute interpreter path.

The interpreter must be the Python 3.11.x series. Dependencies come from `requirements.lock.txt` (pinned, including transitive packages); `requirements-dpd.txt` is DPD-only and must stay out of this environment. Details: `docs/依赖与运行时契约.md`. Do not install dependencies at runtime and do not run bare `pip install <package>` into this environment. Never hand-edit or `pip freeze >` the lock file: change dependencies, then regenerate it with `tools/export_dependency_lock.py` and run the full test suite.

## Commands

Run tests with:

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest discover -s tests -v
```

Or use the repository wrapper:

```powershell
./run_tests.bat
```

Verify dependencies and the application with:

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" tools/export_dependency_lock.py --check
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --check
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --validate-config
```

Before running commands, verify that `D:\Anaconda\envs\Auto_test\python.exe` exists. If it does not exist, report the missing environment instead of trying `base` or installing dependencies into another environment.
