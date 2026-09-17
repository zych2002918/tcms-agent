# =============================================================================
#  公共引导模块：被 start.ps1 / agent-demo.ps1 / test.ps1 共同引用。
#
#  编码：本文件必须为 UTF-8 with BOM（PowerShell 5.1 无 BOM 时按 ANSI 解析，中文乱码）。
# =============================================================================

function Say  ($m) { Write-Host "  $m" }
function Ok   ($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Fail ($m) { Write-Host "  [X]  $m" -ForegroundColor Red }

function Show-Banner($title) {
    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host "    $title" -ForegroundColor Cyan
    Write-Host "  ============================================================" -ForegroundColor Cyan
    Write-Host ""
}

#: 解析出可用的 uv 命令。返回命令字符串，失败返回 $null。
function Find-Uv($Root) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Ok "已安装 uv：$((Get-Command uv).Source)"
        return 'uv'
    }
    $bundled = Join-Path $Root '_offline\uv.exe'
    if (Test-Path $bundled) {
        Ok '使用包内自带的 uv（无需联网安装）'
        return $bundled
    }
    Warn '未找到 uv，尝试联网安装（仅需一次，约 30 秒）...'
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $cand = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
        if (Test-Path $cand) { return $cand }
        if (Get-Command uv -ErrorAction SilentlyContinue) { return 'uv' }
    } catch {
        Warn "自动安装失败：$($_.Exception.Message)"
    }
    return $null
}

function Show-UvHelp {
    Write-Host ""
    Fail '无法获得 uv，请任选一种方式后重试：'
    Say '1) 联网后重新双击本脚本（会自动安装 uv）'
    Say '2) 手动安装：powershell -c "irm https://astral.sh/uv/install.ps1 | iex"'
    Say '3) 下载 uv.exe 放到本目录的 _offline\ 子目录下'
    Say '   下载地址：https://github.com/astral-sh/uv/releases'
    Write-Host ""
}

#: 同步依赖；返回 $true 表示成功。
function Sync-Deps($Uv, $Root) {
    # 注意：这里不能把变量叫 $args —— 那是 PowerShell 的自动变量，
    # 在同名函数里会被当作"未绑定参数"而语义含糊（当前能跑，但是个陷阱）。
    $uvArgs = @('sync', '--quiet')
    if ($env:TCMS_OFFLINE -eq '1') {
        $cache = Join-Path $Root '_offline\uv-cache'
        if (Test-Path $cache) {
            $env:UV_CACHE_DIR = $cache
            $uvArgs += '--offline'
            Warn "离线模式（使用包内缓存：$cache）"
        } else {
            Warn '指定了离线模式，但包内没有 _offline\uv-cache；将尝试联网'
        }
    }
    & $Uv @uvArgs
    if ($LASTEXITCODE -eq 0) { return $true }
    Write-Host ""
    Fail '依赖同步失败。常见原因与处理：'
    Say '· 无网络：首次运行需联网下载 Python 与依赖'
    Say '  （若拿到含 _offline\uv-cache 的完整离线包，设 TCMS_OFFLINE=1 后重试）'
    Say '· 代理问题：确认本机代理可用，或清除 git/uv 的代理设置'
    Say '· 权限问题：把本目录移到有写权限的位置（如 D:\ 或桌面）后重试'
    Write-Host ""
    return $false
}
