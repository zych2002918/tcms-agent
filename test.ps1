# =============================================================================
#  TCMS × AI —— 自检与测试：确认这台机器上一切正常
#
#  跑四件事：环境自检 → 全量回归 → 静态检查 → 端到端评测基线
#  编码：本文件必须为 UTF-8 with BOM。
# =============================================================================

$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
Set-Location $Root
. (Join-Path $Root '_tools\common.ps1')

$env:TCMS_UPSTREAM_DIR  = Join-Path $Root 'packages\engine'
$env:TCMS_UPSTREAM_ROOT = $env:TCMS_UPSTREAM_DIR

Show-Banner 'TCMS × AI —— 自检与测试'

$Uv = Find-Uv $Root
if (-not $Uv) { Show-UvHelp; Read-Host '按回车键退出'; exit 1 }
if (-not (Sync-Deps $Uv $Root)) { Read-Host '按回车键退出'; exit 1 }

$failed = 0

Say '[1/4] 环境自检...'
& $Uv run tcms-agent doctor
if ($LASTEXITCODE -ne 0) { $failed++ }

Write-Host ''
Say '[2/4] 全量回归（约 5 分钟；预期 1615 通过 + 4 条件性跳过）...'
& $Uv run pytest -q -rs --no-header -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { $failed++ }

Write-Host ''
Say '[3/4] 静态检查...'
& $Uv run ruff check packages
if ($LASTEXITCODE -ne 0) { $failed++ }

Write-Host ''
Say '[4/4] 端到端评测（基线：规则臂须达 10/11 且零幻觉引用）...'
& $Uv run tcms-agent eval run --arms rule
if ($LASTEXITCODE -ne 0) { $failed++ }

Write-Host ''
if ($failed -eq 0) {
    Ok '全部通过：这台机器上的 TCMS × AI 可正常工作。'
} else {
    Fail "$failed 项未通过，请把上面的输出反馈。"
}
Write-Host ''
Read-Host '按回车键退出'
