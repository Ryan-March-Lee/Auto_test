param(
    [string[]]$UnittestArgs = @("discover", "-s", "tests", "-v")
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DefaultPython = "C:\My_Document\Anaconda\envs\Auto_test\python.exe"
$Python = if ($env:AUTO_TEST_PYTHON) { $env:AUTO_TEST_PYTHON } else { $DefaultPython }

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Auto_test Python was not found: $Python. Set AUTO_TEST_PYTHON or repair the environment."
}

$Version = & $Python -c 'import sys; print(sys.version.split()[0])'
Write-Host "Python: $Python"
Write-Host "Version: $Version"

Push-Location -LiteralPath $ProjectRoot
try {
    & $Python -m compileall -q -f -x '__pycache__|test_results' .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python -m unittest @UnittestArgs
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
