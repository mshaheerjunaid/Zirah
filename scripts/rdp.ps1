# Readout protection (RDP) for the STM32F407 (Windows).
#
#   powershell -ExecutionPolicy Bypass -File scripts\rdp.ps1 -Level Status
#   powershell -ExecutionPolicy Bypass -File scripts\rdp.ps1 -Level 1
#   powershell -ExecutionPolicy Bypass -File scripts\rdp.ps1 -Level 0
#
# Why it matters: the ATECC608 cannot store an ML-DSA key, so the
# post-quantum key sits in ordinary flash (sector 11). At level 0 anyone
# with the ST-LINK port can read that flash. Status reports the level.
#
# Level 1: the debugger can no longer read the flash, so the ML-DSA key in
#          sector 11 cannot be copied with an ST-LINK. The firmware keeps
#          running and signing. You also cannot reflash until you go back
#          to level 0.
# Level 0: going back from level 1 MASS-ERASES the whole chip. The firmware
#          AND the ML-DSA key are destroyed. You then reflash and run keygen
#          again, which creates a new key with a new fingerprint.
#
# Level 2 is permanent (the board could never be reprogrammed again), so this
# script refuses to set it.
param([Parameter(Mandatory = $true)][ValidateSet("Status", "0", "1")][string]$Level)

$candidates = @(
    "$env:ProgramFiles\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe",
    "${env:ProgramFiles(x86)}\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
)
$cli = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $cli) {
    $cmd = Get-Command STM32_Programmer_CLI.exe -ErrorAction SilentlyContinue
    if ($cmd) { $cli = $cmd.Source }
}
if (-not $cli) { Write-Host "STM32CubeProgrammer not found." -ForegroundColor Red; exit 1 }

if ($Level -eq "Status") {
    $out = & $cli -c port=SWD -ob displ 2>&1 | Out-String
    $line = ($out -split "`n" | Where-Object { $_ -match "RDP" } | Select-Object -First 1)
    if (-not $line) { Write-Host "Could not read the option bytes. Is the ST-LINK cable plugged in?" -ForegroundColor Red; exit 1 }
    Write-Host $line.Trim()
    if ($line -match "0xAA") {
        Write-Host "Level 0: the debugger can read flash, including the ML-DSA key in sector 11." -ForegroundColor Yellow
        Write-Host "Turn on level 1 with: scripts\rdp.ps1 -Level 1"
    } elseif ($line -match "0xCC") {
        Write-Host "Level 2 (permanent): the debug port is disabled for good." -ForegroundColor Yellow
    } else {
        Write-Host "Level 1: the debugger cannot read flash. The key stays on the board." -ForegroundColor Green
    }
    exit 0
}
if ($Level -eq "1") {
    Write-Host "This turns on readout protection level 1." -ForegroundColor Yellow
    Write-Host "Afterwards you cannot reflash the board until you return to level 0,"
    Write-Host "and returning to level 0 erases the firmware and the ML-DSA key."
    $ans = Read-Host "Type YES to continue"
    if ($ans -ne "YES") { Write-Host "Cancelled."; exit 1 }
    & $cli -c port=SWD -ob RDP=0xBB
} else {
    Write-Host "Returning to level 0 MASS-ERASES the chip: firmware and ML-DSA key are destroyed." -ForegroundColor Yellow
    Write-Host "Export the trusted public keys first (zirah export-keys old-keys.json) if you"
    Write-Host "still want to verify signatures made with the old key."
    $ans = Read-Host "Type ERASE to continue"
    if ($ans -ne "ERASE") { Write-Host "Cancelled."; exit 1 }
    & $cli -c port=SWD -ob RDP=0xAA
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "STM32CubeProgrammer reported an error (exit code $LASTEXITCODE)." -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "Done. Unplug both USB cables, wait 5 seconds, and plug them back in." -ForegroundColor Green
