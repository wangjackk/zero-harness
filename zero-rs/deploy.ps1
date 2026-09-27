# zero-rs 一键部署到车端 (hiwonder): 交叉编译 -> 停旧进程 -> 上传 -> 起 + 重试.
#
# 用法:
#   .\deploy.ps1                    # 编译 + 部署 + 起进程
#   .\deploy.ps1 -SkipBuild         # 不编译, 用上次产物直接部署
#
# 前置: ssh Host `hiwonder108` 可免密登录; kernel 已绑 0.0.0.0:7777.
# 半开连接: pkill 后立刻起会被拒一次, kernel 随即自动清理死 peer
# (nodes cleaned), 本脚本内置重试, 无需手工干预.
param(
    [string]$SshHost = 'hiwonder108',
    [string]$KernelAddr = '192.168.1.127:7777',
    [string]$HubId = 'car-rs',
    [string]$RemotePath = '/home/hiwonder/zero-rs',
    [string]$RemoteLog = '/tmp/zero-rs.log',
    [string]$EnvFile = "$PSScriptRoot\..\zero-py\.env",
    [string]$ZigRoot = 'C:\Users\wjk\AppData\Local\Microsoft\WinGet\Packages\zig.zig_Microsoft.Winget_Source_8wekyb3d8bbwe\zig-x86_64-windows-0.16.0',
    [string]$Pkgconf = 'E:\code\pyfiles\sherpa-onnx\services\_dev\cross\pkgconf-pkg\PFiles64\pkgconf-3.0.7',
    [string]$Sysroot = 'E:\code\pyfiles\sherpa-onnx\services\_dev\cross\zig-sysroot\sysroot',
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$target = 'aarch64-unknown-linux-gnu.2.27'
$bin = "$PSScriptRoot\target\aarch64-unknown-linux-gnu\release\zero-rs"

if (-not $SkipBuild) {
    Write-Host "[1/4] zigbuild $target" -ForegroundColor Cyan
    $env:PATH = "$ZigRoot;$Pkgconf;$env:PATH"
    $env:PKG_CONFIG_PATH = "$Sysroot\usr\lib\aarch64-linux-gnu\pkgconfig"
    $env:PKG_CONFIG_SYSROOT_DIR = $Sysroot
    $env:PKG_CONFIG_ALLOW_CROSS = '1'
    $env:RUSTFLAGS = "-L native=$Sysroot\usr\lib\aarch64-linux-gnu"
    cargo zigbuild --release --target $target
    if ($LASTEXITCODE -ne 0) { throw "build failed" }
}

if (-not (Test-Path $bin)) { throw "binary not found: $bin (build first)" }

$ttsKey = (Get-Content $EnvFile | Select-String '^SEED_TTS_API_KEY=').Line -replace '.*=', ''
if (-not $ttsKey) { throw "SEED_TTS_API_KEY not found in $EnvFile" }

Write-Host "[2/4] stop remote process" -ForegroundColor Cyan
ssh $SshHost "pkill -x zero-rs" 2>$null

Write-Host "[3/4] upload binary" -ForegroundColor Cyan
scp $bin "${SshHost}:$RemotePath"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }

Write-Host "[4/4] start + verify" -ForegroundColor Cyan
$start = "setsid nohup env SEED_TTS_API_KEY=$ttsKey $RemotePath $KernelAddr $HubId > $RemoteLog 2>&1 < /dev/null &"
for ($i = 1; $i -le 3; $i++) {
    ssh $SshHost $start
    Start-Sleep -Seconds 5
    $log = ssh $SshHost "tail -5 $RemoteLog"
    if ($log -match 'catalog\.pushed') {
        $log | Where-Object { $_ -match 'catalog|skipped' } | Write-Host
        Write-Host "deployed: hub '$HubId' -> $SshHost, dialing $KernelAddr" -ForegroundColor Green
        exit 0
    }
    if ($log -match 'already connected') {
        Write-Host "attempt $i rejected (stale peer), waiting for kernel cleanup..." -ForegroundColor Yellow
        ssh $SshHost "pkill -x zero-rs" 2>$null
        Start-Sleep -Seconds 4
        continue
    }
    $log | Write-Host
    throw "unexpected state, see $RemoteLog on $SshHost"
}
throw "still rejected after 3 attempts; check kernel peer state"
