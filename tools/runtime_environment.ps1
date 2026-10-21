param(
    [string]$Purpose = "project command"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"

if (-not $env:AUTO_TEST_PYTHON -and (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    foreach ($Line in Get-Content -LiteralPath $EnvFile) {
        if ($Line -match '^\s*AUTO_TEST_PYTHON\s*=\s*(.*?)\s*$') {
            $env:AUTO_TEST_PYTHON = $Matches[1].Trim().Trim('"').Trim("'")
            break
        }
    }
}

if (-not $env:AUTO_TEST_PYTHON) {
    throw "AUTO_TEST_PYTHON is not set for $Purpose. Configure it in .env."
}
if (-not (Test-Path -LiteralPath $env:AUTO_TEST_PYTHON -PathType Leaf)) {
    throw "Python interpreter was not found: $env:AUTO_TEST_PYTHON"
}

$ConfiguredPython = [System.IO.Path]::GetFullPath($env:AUTO_TEST_PYTHON)
$ActualPython = (& $ConfiguredPython -c "import sys; print(sys.executable)").Trim()
if ([System.IO.Path]::GetFullPath($ActualPython) -ine $ConfiguredPython) {
    throw "Configured interpreter did not launch itself: $ConfiguredPython"
}

# Run ID: use the explicit value when provided, otherwise generate it once
# here and pass it to every child process of this run so evidence stays linkable.
if (-not $env:AUTO_TEST_RUN_ID) {
    $env:AUTO_TEST_RUN_ID = "pa-auto-test-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
}

Write-Host "Runtime: $Purpose"
Write-Host "Python: $ConfiguredPython"
Write-Host "Run ID: $($env:AUTO_TEST_RUN_ID)"
