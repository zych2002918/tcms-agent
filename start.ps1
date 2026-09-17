# =============================================================================
#  TCMS × AI —— 一键启动（Web UI）
#
#  新电脑上只需双击 start.bat；无需事先安装 Python（uv 会自动准备解释器）。
#  编码：本文件必须为 UTF-8 with BOM。
# =============================================================================

$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
Set-Location $Root
. (Join-Path $Root '_tools\common.ps1')

if (-not $env:PORT) { $env:PORT = '8000' }
$env:TCMS_UPSTREAM_DIR  = Join-Path $Root 'packages\engine'
$env:TCMS_UPSTREAM_ROOT = $env:TCMS_UPSTREAM_DIR

Show-Banner 'TCMS × AI —— 列车控制软件智能测试平台 + AI 测试工程师 Agent'

Say '[1/4] 检查运行环境（uv）...'
$Uv = Find-Uv $Root
if (-not $Uv) { Show-UvHelp; Read-Host '按回车键退出'; exit 1 }

Say '[2/4] 同步依赖（首次运行需数分钟，之后秒开）...'
if (-not (Sync-Deps $Uv $Root)) { Read-Host '按回车键退出'; exit 1 }
Ok '依赖就绪'

Say '[3/4] 环境自检...'
& $Uv run python -c "import tcms, tcms_ai_platform, tcms_ai_testgen, tcms_agent" 2>$null
if ($LASTEXITCODE -eq 0) {
    Ok '四个成员包（引擎 / 平台 / 生成器 / Agent）均可导入'
} else {
    Warn '成员包导入异常，仍将尝试启动；若启动失败请把本窗口内容截图反馈'
}

Write-Host ''
Say "[4/4] 启动服务 → http://127.0.0.1:$env:PORT"
Say '浏览器将在约 2 秒后自动打开；关闭本窗口即停止服务。'
Say "换端口：先在 PowerShell 执行  `$env:PORT='8001'  再运行本脚本。"
Write-Host ''

$null = Start-Process powershell -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-Command', "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:$($env:PORT)'"
)

& $Uv run python -m uvicorn tcms_ai_platform.server.app:app --host 127.0.0.1 --port $env:PORT

Write-Host ''
Say '服务已停止。'
Read-Host '按回车键退出'
