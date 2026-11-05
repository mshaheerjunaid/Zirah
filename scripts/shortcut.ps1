# Creates a Zirah shortcut that starts the app with no console window.
# Called by setup.bat with ZIRAH_DIR (the Zirah folder) and ZIRAH_WHERE
# (start or desktop) in the environment, so no path is ever re-quoted.
$dir = $env:ZIRAH_DIR
if (-not $dir -or -not (Test-Path (Join-Path $dir "ui\zirah_app.py"))) {
    Write-Host "  Zirah folder not found; no shortcut created." -ForegroundColor Yellow
    exit 1
}
if ($env:ZIRAH_WHERE -eq "desktop") {
    $folder = [Environment]::GetFolderPath("Desktop")
} else {
    $folder = Join-Path ([Environment]::GetFolderPath("Programs")) "Zirah"
    New-Item -ItemType Directory -Force -Path $folder | Out-Null
}
$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut((Join-Path $folder "Zirah.lnk"))
$lnk.TargetPath = Join-Path $dir ".venv\Scripts\pythonw.exe"
$lnk.Arguments = '"' + (Join-Path $dir "ui\zirah_app.py") + '"'
$lnk.WorkingDirectory = $dir
$lnk.IconLocation = (Join-Path $dir "ui\zirah.ico") + ",0"
$lnk.Description = "Zirah Post-Quantum File Signer"
$lnk.Save()
Write-Host "  Shortcut created in $folder"
