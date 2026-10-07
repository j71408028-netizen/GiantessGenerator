param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

$Entry = "main.py"
$Name = "GiantessGenerator"

& $Python -m venv .venv-build
& .\.venv-build\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-build\Scripts\python.exe -m pip install -r requirements.txt pyinstaller

# 单一打包形态：入口跟随设置里的「启动界面模式」，两套界面共用一个进程模型，
# 因此全部依赖（含 networkx 依赖图）一并收集。
& .\.venv-build\Scripts\pyinstaller.exe `
    --noconfirm `
    --clean `
    --windowed `
    --name $Name `
    --icon assets\icons\icon.ico `
    --add-data "assets;assets" `
    --add-data "data\packs;data\packs" `
    --add-data "data\static;data\static" `
    --collect-all customtkinter `
    --collect-all dearpygui `
    --collect-all PIL `
    --collect-all openai `
    --collect-all networkx `
    --collect-all numpy `
    --collect-all webview `
    $Entry

Write-Host "Created: $Root\dist\$Name\$Name.exe"
Write-Host "The package excludes user settings and archives. They are created in the local application-data directory."
