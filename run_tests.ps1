param(
    [string]$Layer = "Offline",
    [switch]$Parallel,
    [int]$Workers = 0,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$UnittestArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RuntimeHelper = Join-Path $ProjectRoot "tools\runtime_environment.ps1"
. $RuntimeHelper -Purpose "offline tests"
$Python = $env:AUTO_TEST_PYTHON

$Version = & $Python -c 'import sys; print(sys.version.split()[0])'
Write-Host "Version: $Version"

Push-Location -LiteralPath $ProjectRoot
try {
    # Compile in memory so locked or read-only __pycache__ files cannot block
    # the test entry point.
    & $Python tools\compile_check.py $ProjectRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    # Dependencies must match the committed lock file; report drift immediately
    # instead of failing deep inside the suite.
    & $Python tools\export_dependency_lock.py --check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    # The full Offline discovery already includes the static quality gates.
    # Running them here as well duplicated their repository-wide AST scans.
    # Ui/Integration run every gate explicitly below; Unit/Simulation keep
    # the static pre-check because they do not discover the gate modules.
    if ($Layer -eq "Unit" -or $Layer -eq "Simulation") {
        & $Python -m unittest tests.test_static_quality_gate tests.test_exception_taxonomy -v
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    # Repository gates: every change surface must pass them.  The Ui and
    # Integration layers run them before their own tests.
    $GateModules = @(
        "tests.test_architecture_dependencies",
        "tests.test_presentation_boundaries",
        "tests.test_public_api_inventory",
        "tests.test_exception_taxonomy",
        "tests.test_static_quality_gate",
        "tests.test_runtime_dependency_contract"
    )

    $KnownLayers = @("Offline", "Unit", "Simulation", "Ui", "Integration")
    if ($Layer -notin $KnownLayers) {
        # Preserve the original positional form, for example:
        # run_tests.ps1 discover -s tests -v
        $TestCommand = @($Layer) + @($UnittestArgs)
    } elseif ($UnittestArgs -and $UnittestArgs.Count -gt 0) {
        $TestCommand = $UnittestArgs
    } elseif ($Layer -eq "Unit") {
        $TestCommand = @("discover", "-s", "tests\unit", "-p", "test_*.py", "-v")
    } elseif ($Layer -eq "Simulation") {
        $TestCommand = @("discover", "-s", "tests\simulation", "-p", "test_*.py", "-v")
    } elseif ($Layer -eq "Ui") {
        & $Python -m unittest @GateModules -v
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Python -m unittest discover -s tests\ui -t . -p "test_*.py" -v
        exit $LASTEXITCODE
    } elseif ($Layer -eq "Integration") {
        & $Python -m unittest @GateModules -v
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Python -m unittest discover -s tests\ui -t . -p "test_*.py" -v
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Python -m unittest discover -s tests\integration -t . -p "test_*.py" -v
        exit $LASTEXITCODE
    } else {
        # Hardware tests live outside tests/ and are never part of discovery.
        $TestCommand = @("discover", "-s", "tests", "-v")
    }

    if ($Parallel -and $Layer -eq "Offline" -and (-not $UnittestArgs)) {
        $ParallelArgs = @("-m", "pytest", "-n")
        if ($Workers -gt 0) {
            $ParallelArgs += "$Workers"
        } else {
            # Cap at 4: more workers add memory pressure (Qt/matplotlib) faster
            # than they save wall time on this suite.
            $ParallelArgs += "$([Math]::Min(4, [Environment]::ProcessorCount))"
        }
        # Match unittest discover: hardware/ is exercised only by the
        # dedicated hardware-smoke entry point.  The Qt two-phase flow and
        # repository-wide static scan stay serial: Qt can terminate a worker
        # process, and the scan is a global baseline check.
        $ParallelArgs += @(
            "--dist", "loadfile", "tests",
            "--ignore", "tests\hardware",
            "--ignore", "tests\integration\test_gui_two_phase_flow.py",
            "--ignore", "tests\test_static_quality_gate.py"
        )
        & $Python @ParallelArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Python -m unittest tests.integration.test_gui_two_phase_flow tests.test_static_quality_gate tests.test_exception_taxonomy -v
    } else {
        & $Python -m unittest @TestCommand
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
