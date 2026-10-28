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

# Python 版本契约与 infrastructure/runtime/runtime_contract.py 的
# REQUIRED_PYTHON_SERIES 保持一致：测试、编译、启动检查和发布清单只认 3.11。
$PythonSeries = (& $ConfiguredPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($PythonSeries -ne "3.11") {
    throw "Python 3.11.x is required for $Purpose, configured interpreter reports $PythonSeries."
}

# conda 环境的原生依赖（freetype、libpng、openssl 等 DLL）位于 <env>\Library\bin 等目录。
# 只按绝对路径调用 python.exe 而不激活环境时，这些目录不在 PATH 上：纯 Python 逻辑正常，
# 但 matplotlib 渲染与 Qt 插件会在延迟加载阶段直接崩溃（退出码 0xC06D007F）。
# 这里按 conda 的标准目录布局前置 PATH，使脚本不依赖调用者是否已 activate。
$EnvPrefix = Split-Path -Parent $ConfiguredPython
$RuntimeDirectories = @(
    $EnvPrefix
    (Join-Path $EnvPrefix "Library\mingw-w64\bin")
    (Join-Path $EnvPrefix "Library\usr\bin")
    (Join-Path $EnvPrefix "Library\bin")
    (Join-Path $EnvPrefix "Scripts")
    (Join-Path $EnvPrefix "bin")
)
$ExistingPathEntries = @(
    ($env:PATH -split ';') |
        Where-Object { $_ } |
        ForEach-Object { $_.TrimEnd('\') }
)
$AddedRuntimeDirectories = @(
    $RuntimeDirectories |
        Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
        Where-Object { $ExistingPathEntries -notcontains $_.TrimEnd('\') }
)
if ($AddedRuntimeDirectories.Count -gt 0) {
    $env:PATH = (@($AddedRuntimeDirectories) + @($ExistingPathEntries) -join ';')
    Write-Host "Runtime PATH: 已前置 $(@($AddedRuntimeDirectories).Count) 个环境目录（无需预先 activate）"
}

# Run ID: use the explicit value when provided, otherwise generate it once
# here and pass it to every child process of this run so evidence stays linkable.
if (-not $env:AUTO_TEST_RUN_ID) {
    $env:AUTO_TEST_RUN_ID = "pa-auto-test-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
}

Write-Host "Runtime: $Purpose"
Write-Host "Python: $ConfiguredPython"
Write-Host "Run ID: $($env:AUTO_TEST_RUN_ID)"
