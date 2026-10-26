# 停止运行目录里的启动器实例（按可执行文件路径匹配，避开中文进程名的编码问题）
# 改成你自己的运行目录
$runtimeDir = 'D:\LocalModels\AIbot机器人\启动器'
$target = Join-Path $runtimeDir '大肥鱼启动器.exe'
$stopped = 0
Get-Process -ErrorAction SilentlyContinue | ForEach-Object {
    $path = $null
    try { $path = $_.Path } catch { }
    if ($path -and ($path -ieq $target -or $path.StartsWith($runtimeDir, [System.StringComparison]::OrdinalIgnoreCase))) {
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; $stopped++ } catch { }
    }
}
Start-Sleep -Seconds 2
Write-Output ("stopped=" + $stopped)