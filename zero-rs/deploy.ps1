# zero-rs 一键部署到车端 (hiwonder): 交叉编译 -> 上传 -> 重启 systemd 单元 + 验证.
#
# 用法:
#   .\deploy.ps1                    # 编译 + 部署 + 重启单元
#   .\deploy.ps1 -SkipBuild         # 不编译, 用上次产物直接部署
#
# 前置: ssh Host `hiwonder108` 可免密登录; 车端 zero-rs.service 已安装.
# 连接参数与环境变量在单元与 ~/zero-rs/zero-rs.env, 本脚本不再传参.
# 半开连接: 重启后旧 peer 会拒一次, 单元 Restart=always(10s) 自愈,
# 本脚本只需轮询日志等收敛.
param(
    [string]$SshHost = 'hiwonder108',
    [string]$RemotePath = '/home/hiwonder/zero-rs/zero-rs',
    [string]$RemoteLog = '/tmp/zero-rs.log',
    [string]$ZigRoot = 'C:\Users\wjk\AppData\Local\Microsoft\WinGet\Packages\zig.zig_Microsoft.Winget_Source_8wekyb3d8bbwe\zig-x86_64-windows-0.16.0',
    [string]$Pkgconf = 'E:\code\pyfiles\sherpa-onnx\services\_dev\cross\pkgconf-pkg\PFiles64\pkgconf-3.0.7',
    [string]$Sysroot = 'E:\code\pyfiles\sherpa-onnx\services\_dev\cross\zig-sysroot\sysroot',
    [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$target = 'aarch64-unknown-linux-gnu.2.27'
$bin = "$PSScriptRoot\target\aarch64-unknown-linux-gnu\release\zero-rs"

if (-not $SkipBuild) {
    Write-Host "[1/3] zigbuild $target" -ForegroundColor Cyan
    $env:PATH = "$ZigRoot;$Pkgconf;$env:PATH"
    $env:PKG_CONFIG_PATH = "$Sysroot\usr\lib\aarch64-linux-gnu\pkgconfig"
    $env:PKG_CONFIG_SYSROOT_DIR = $Sysroot
    $env:PKG_CONFIG_ALLOW_CROSS = '1'
    $env:RUSTFLAGS = "-L native=$Sysroot\usr\lib\aarch64-linux-gnu"
    cargo zigbuild --release --target $target
    if ($LASTEXITCODE -ne 0) { throw "build failed" }
}

if (-not (Test-Path $bin)) { throw "binary not found: $bin (build first)" }

Write-Host "[2/3] upload binary" -ForegroundColor Cyan
# 直接覆盖运行中的二进制会 ETXTBSY: 先传 .new 再 mv 原子替换(rename 不受
# 运行中限制), 服务全程不断; 重启单元后新 inode 生效.
scp $bin "${SshHost}:$RemotePath.new"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }
ssh $SshHost "mv -f $RemotePath.new $RemotePath && chmod +x $RemotePath"

Write-Host "[3/3] restart unit + verify" -ForegroundColor Cyan
# 清日志防误读旧成功行, 再重启单元拉新二进制
ssh $SshHost "truncate -s 0 $RemoteLog && sudo systemctl restart zero-rs"
for ($i = 1; $i -le 6; $i++) {
    Start-Sleep -Seconds 5
    $log = ssh $SshHost "tail -5 $RemoteLog"
    if ($log -match 'catalog\.pushed') {
        $log | Where-Object { $_ -match 'catalog|skipped' } | Write-Host
        Write-Host "deployed: zero-rs unit restarted on $SshHost" -ForegroundColor Green
        exit 0
    }
    if ($log -match 'already connected') {
        Write-Host "attempt $i rejected (stale peer), unit auto-retries in 10s..." -ForegroundColor Yellow
        continue
    }
    $log | Write-Host
    throw "unexpected state, see $RemoteLog on $SshHost"
}
throw "not converged after 6 attempts; check unit on ${SshHost}: journalctl -u zero-rs"
