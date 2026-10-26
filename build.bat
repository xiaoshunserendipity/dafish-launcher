@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo [1/3] 检查虚拟环境...
if not exist ".venv\Scripts\python.exe" (
    echo   未找到 .venv，正在创建...
    C:\Python\python.exe -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "PySide6-Essentials==6.11.2" "shiboken6==6.11.2" pyinstaller psutil || goto :error
)

echo [2/3] 运行自检...
".venv\Scripts\python.exe" launcher.py --selftest
if errorlevel 1 (
    echo   自检失败，停止打包。
    goto :error
)

echo [3/3] 打包单文件 exe...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean ^
    --onefile --noconsole ^
    --name "AstrBotLauncher" ^
    --icon "assets\app.ico" --version-file "version_info.txt" ^
    --add-data "assets;assets" ^
    --hidden-import psutil ^
    launcher.py || goto :error

if exist "dist\AstrBotLauncher.exe" (
    copy /y "dist\AstrBotLauncher.exe" "dist\大肥鱼启动器.exe" >nul
    del /q "dist\AstrBotLauncher.exe" >nul 2>&1
)
if exist "build" rmdir /s /q "build" >nul 2>&1

rem ===== 4) 同步到运行目录并重启（如不需要可删掉这一段）=====
rem 改成你自己的运行目录；留空则跳过同步
set "RUNTIME_DIR=D:\LocalModels\AIbot机器人\启动器"
if exist "%RUNTIME_DIR%\" (
    echo [4/4] 同步到运行目录并重启...
    powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0stop_runtime.ps1" >nul 2>&1
    ping -n 2 127.0.0.1 >nul
    copy /y "dist\大肥鱼启动器.exe" "%RUNTIME_DIR%\大肥鱼启动器.exe" >nul
    powershell -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath '%RUNTIME_DIR%\大肥鱼启动器.exe' -WorkingDirectory '%RUNTIME_DIR%'" >nul 2>&1
    echo   已更新并启动：%RUNTIME_DIR%\大肥鱼启动器.exe
) else (
    echo [4/4] 未找到运行目录，跳过同步：%RUNTIME_DIR%
)

echo.
echo 完成：dist\大肥鱼启动器.exe
if not defined LAUNCHER_NO_PAUSE pause
exit /b 0

:error
echo.
echo 构建失败，请查看上面的输出。
if not defined LAUNCHER_NO_PAUSE pause
exit /b 1
