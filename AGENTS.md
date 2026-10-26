# Agent Execution Rules

## Python Environment

This project must use the following Python interpreter:

```text
D:\Anaconda\envs\Auto_test\python.exe
```

The Conda environment name is `Auto_test`. Do not use the Anaconda `base` environment, a bare `python` command, or a different interpreter path for project commands. Agent/tool processes may not inherit the user's activated Conda environment, so prefer the absolute interpreter path.

## Commands

Run tests with:

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest discover -s tests -v
```

Or use the repository wrapper:

```powershell
./run_tests.bat
```

Run the application checks with:

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --check
& "D:\Anaconda\envs\Auto_test\python.exe" launcher.py --validate-config
```

Before running commands, verify that `D:\Anaconda\envs\Auto_test\python.exe` exists. If it does not exist, report the missing environment instead of trying `base` or installing dependencies into another environment.
