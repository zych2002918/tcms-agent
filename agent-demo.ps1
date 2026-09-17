# =============================================================================
#  TCMS × AI —— Agent 演示：让 AI 测试工程师现场跑一遍完整链路
#
#  全程离线、不需要任何 API key：走"离线规则臂"，与真模型走同一张图、
#  同一套工具、同一套权限与审计。
#  编码：本文件必须为 UTF-8 with BOM。
# =============================================================================

$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
Set-Location $Root
. (Join-Path $Root '_tools\common.ps1')

$env:TCMS_UPSTREAM_DIR  = Join-Path $Root 'packages\engine'
$env:TCMS_UPSTREAM_ROOT = $env:TCMS_UPSTREAM_DIR

Show-Banner 'AI 测试工程师 Agent —— 演示（离线可跑，无需任何密钥）'

$Uv = Find-Uv $Root
if (-not $Uv) { Show-UvHelp; Read-Host '按回车键退出'; exit 1 }
if (-not (Sync-Deps $Uv $Root)) { Read-Host '按回车键退出'; exit 1 }

Say '将依次演示四件事，每件都会真实执行（不是演示图）：'
Say '  ① 验证一个安全故障的处置语义并给出资产引用'
Say '  ② 用真实引擎确认该故障确实触发该处置'
Say '  ③ 让 Agent 自己写一条回归用例并真跑'
Say '  ④ 把结论沉淀进长期记忆（此步需人工审批，演示中自动拒绝）'
Write-Host ''

$goal = '验证车门故障必须触发降级处置'
Say "目标：$goal"
Write-Host ('  ' + ('-' * 68))
& $Uv run tcms-agent run $goal
Write-Host ('  ' + ('-' * 68))
Write-Host ''
Say '若要看 Agent 的完整能力清单与用法：'
Say '  uv run tcms-agent --help'
Say '  uv run tcms-agent tools          （列出 15 个工具与四级权限）'
Say '  uv run tcms-agent eval run       （跑端到端评测）'
Say '  uv run tcms-agent nolib --verify （框架版 vs 手写最小 loop 对照）'
Write-Host ''
Read-Host '按回车键退出'
