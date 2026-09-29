param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath,
    [switch]$ConfirmHardwareSmoke
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

$Python = $env:AUTO_TEST_PYTHON
if (-not $Python) {
    throw "AUTO_TEST_PYTHON is not set. Configure it in .env before running hardware smoke."
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python interpreter was not found: $Python"
}

$ResolvedConfig = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $ConfigPath))
$HardwareRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot "hardware"))
if (-not (Test-Path -LiteralPath $ResolvedConfig -PathType Leaf)) {
    throw "Hardware smoke configuration was not found: $ConfigPath"
}
if (-not $ResolvedConfig.StartsWith($HardwareRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Hardware smoke configuration must be under hardware/."
}
if ([System.IO.Path]::GetFileName($ResolvedConfig) -match 'example|sample|template') {
    throw "Example or template configuration cannot be executed: $ConfigPath"
}

try {
    $Config = Get-Content -LiteralPath $ResolvedConfig -Raw | ConvertFrom-Json
} catch {
    throw "Invalid hardware smoke JSON: $ConfigPath"
}
if ($Config.environment -ne "hardware_smoke") {
    throw "Hardware smoke configuration must set environment to hardware_smoke."
}
if ($Config.require_user_confirmation -ne $true) {
    throw "Hardware smoke configuration must require user confirmation."
}
if ($Config.require_empty_setup -ne $true) {
    throw "Hardware smoke configuration must require an empty setup."
}
$ConfigText = $Config | ConvertTo-Json -Depth 20 -Compress
if ($ConfigText -match 'REPLACE_WITH_') {
    throw "Hardware smoke configuration still contains placeholder values."
}
if ($Config.devices.signal_generator.address -match 'REPLACE_WITH_' -or
    $Config.devices.spectrum_analyzer.address -match 'REPLACE_WITH_' -or
    $Config.devices.power_supply.address -match 'REPLACE_WITH_') {
    throw "Hardware smoke configuration still contains placeholder device addresses."
}
if ($env:HARDWARE_SMOKE_ENABLED -ne "1") {
    throw "Hardware smoke is disabled. Set HARDWARE_SMOKE_ENABLED=1 explicitly."
}
if ($env:CI -and $env:CI -notmatch '^(0|false|no)$') {
    throw "Hardware smoke is forbidden in CI."
}
if (-not $ConfirmHardwareSmoke) {
    throw "Pass -ConfirmHardwareSmoke only after confirming the equipment is unloaded and the test is authorized."
}

$HardwareTests = Join-Path $ProjectRoot "tests\hardware"
if (-not (Test-Path -LiteralPath $HardwareTests -PathType Container)) {
    throw "No hardware smoke test directory exists yet: tests/hardware"
}

$HardwareTestFiles = @(Get-ChildItem -LiteralPath $HardwareTests -Filter "test_*.py" -File)
if ($HardwareTestFiles.Count -eq 0) {
    throw "No hardware smoke tests are registered under tests/hardware."
}

Write-Host "Hardware smoke gate passed. Config: $ResolvedConfig"
Push-Location -LiteralPath $ProjectRoot
try {
    $env:HARDWARE_SMOKE_CONFIG = $ResolvedConfig
    & $Python -m unittest discover -s tests\hardware -p "test_*.py" -v
    exit $LASTEXITCODE
}
finally {
    Remove-Item Env:HARDWARE_SMOKE_CONFIG -ErrorAction SilentlyContinue
    Pop-Location
}
