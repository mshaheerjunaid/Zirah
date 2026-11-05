# Flash firmware\zirah.bin onto the STM32F4DISCOVERY over the on-board ST-LINK,
# using STM32CubeProgrammer's command-line tool. Normally started by flash.bat.
#
# Only the flash sectors the firmware needs are erased, so the ML-DSA key in
# flash sector 11 survives reflashing.
param(
    [string]$Bin = (Join-Path $PSScriptRoot "..\firmware\zirah.bin")
)

$candidates = @(
    "$env:ProgramFiles\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe",
    "${env:ProgramFiles(x86)}\STMicroelectronics\STM32Cube\STM32CubeProgrammer\bin\STM32_Programmer_CLI.exe"
)
$cli = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $cli) {
    $cmd = Get-Command STM32_Programmer_CLI.exe -ErrorAction SilentlyContinue
    if ($cmd) { $cli = $cmd.Source }
}
if (-not $cli) {
    Write-Host "STM32CubeProgrammer was not found. Install it from st.com (search STM32CubeProg)." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $Bin)) {
    Write-Host "Firmware file not found: $Bin" -ForegroundColor Red
    exit 1
}
$Bin = (Resolve-Path $Bin).Path

# Make sure the firmware file is not damaged (compare with zirah.bin.sha256).
$shaFile = "$Bin.sha256"
if (Test-Path $shaFile) {
    $want = ((Get-Content $shaFile -Raw).Trim() -split '\s+')[0].ToLower()
    $have = (Get-FileHash -Algorithm SHA256 $Bin).Hash.ToLower()
    if ($want -ne $have) {
        Write-Host "zirah.bin does not match zirah.bin.sha256. The file is damaged; download the folder again." -ForegroundColor Red
        exit 1
    }
    Write-Host "Firmware file checked (SHA-256 OK)."
}

Write-Host "Flashing $Bin ..."
& $cli -c port=SWD -w $Bin 0x08000000 -v -rst
if ($LASTEXITCODE -ne 0) {
    Write-Host "Flashing failed. Check the mini-USB cable (ST-LINK socket, CN1) and that" -ForegroundColor Red
    Write-Host "both CN3 jumpers on the board are fitted." -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "Done. The board has restarted and is running Zirah." -ForegroundColor Green
