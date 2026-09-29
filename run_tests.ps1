param(
    [string]$Layer = "Offline",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$UnittestArgs
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $ProjectRoot ".env"
if (-not $env:AUTO_TEST_PYTHON -and (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    foreach ($Line in Get-Content -LiteralPath $EnvFile) {
        if ($Line -match '^\s*AUTO_TEST_PYTHON\s*=\s*(.*?)\s*$') {
            $env:AUTO_TEST_PYTHON = $Matches[1].Trim().Trim('"').Trim("'")
            break
        }
    }
}
$Python = if ($env:AUTO_TEST_PYTHON) {
    $env:AUTO_TEST_PYTHON
} else {
    throw "AUTO_TEST_PYTHON is not set. Configure it in .env before running project commands."
}

if ($Python -match '^(?:[A-Za-z]:[\\/]|\\\\)' -and -not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python interpreter was not found: $Python. Set AUTO_TEST_PYTHON in .env or activate the Auto_test environment."
}

$Version = & $Python -c 'import sys; print(sys.version.split()[0])'
Write-Host "Python: $Python"
Write-Host "Version: $Version"

Push-Location -LiteralPath $ProjectRoot
try {
    # Compile in memory so locked or read-only __pycache__ files cannot block
    # the test entry point.
    & $Python tools\compile_check.py $ProjectRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $KnownLayers = @("Offline", "Unit", "Simulation")
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
    } else {
        # Hardware tests live outside tests/ and are never part of discovery.
        $TestCommand = @("discover", "-s", "tests", "-v")
    }

    & $Python -m unittest @TestCommand
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
