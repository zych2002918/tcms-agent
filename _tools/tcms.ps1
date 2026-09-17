# =============================================================================
#  命令行入口（被 tcms.bat 调用）
#
#  为什么需要它：包内自带 uv，而一台新电脑通常**没有全局 uv**。
#  若照直告诉新手敲 `uv run tcms-agent ...`，会失败在两处：
#    1) 没装 uv        -> 'uv' is not recognized as an internal or external command
#    2) 在别的目录执行  -> error: Failed to spawn: `tcms-agent` / program not found
#  本脚本把这两件事都替用户做掉：先找 uv（PATH → 包内 _offline\uv.exe），
#  再切到包目录，然后原样转发参数。这样就只有一个稳定入口：`tcms <子命令>`。
#
#  编码：本文件必须为 UTF-8 with BOM（PowerShell 5.1 无 BOM 时按 ANSI 解析，中文乱码）。
# =============================================================================

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot   # _tools 的上级 = 包根
Set-Location $Root

# 领域引擎就在本仓内，无需用户配置
$env:TCMS_UPSTREAM_DIR = Join-Path $Root 'packages\engine'
$env:TCMS_UPSTREAM_ROOT = $env:TCMS_UPSTREAM_DIR

# 找 uv：PATH → 包内自带 → 报错并给指引（不静默失败）
$Uv = $null
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $Uv = 'uv'
} elseif (Test-Path (Join-Path $Root '_offline\uv.exe')) {
    $Uv = Join-Path $Root '_offline\uv.exe'
}

if (-not $Uv) {
    Write-Host ""
    Write-Host "  [X] 找不到 uv（PATH 里没有，包内 _offline\uv.exe 也不存在）。" -ForegroundColor Red
    Write-Host "      请任选一种方式后重试："
    Write-Host "      1) 联网后双击一次 start.bat（它会自动安装 uv）"
    Write-Host "      2) powershell -c `"irm https://astral.sh/uv/install.ps1 | iex`""
    Write-Host "      3) 下载 uv.exe 放到本目录的 _offline\ 子目录下"
    Write-Host "         https://github.com/astral-sh/uv/releases"
    Write-Host ""
    exit 1
}

# `uv run` 会在需要时自动同步依赖，因此这里不必显式 sync。
& $Uv run tcms-agent @args
exit $LASTEXITCODE
