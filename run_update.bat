@echo off
chcp 65001 >nul
title 财联社数据更新

:: 从 .env 加载环境变量（跳过 # 注释行和空行）
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /v "^#" .env 2^>nul`) do (
    if not "%%A"=="" set "%%A=%%B"
)

if "%SUPABASE_URL%"=="" (
    echo [错误] .env 文件中未找到 SUPABASE_URL，请先填写 .env
    pause
    exit /b 1
)

echo 正在运行更新脚本...
echo.
python update_cls_data.py

echo.
if %errorlevel% equ 0 (
    echo ========================================
    echo 更新完成！按任意键关闭窗口
    echo ========================================
) else (
    echo ========================================
    echo 运行出错（错误码 %errorlevel%），请查看上方日志
    echo ========================================
)
pause >nul
