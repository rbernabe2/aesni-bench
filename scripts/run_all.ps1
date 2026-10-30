# run_all.ps1 - run every configuration x every data size x N trials.
#
#   .\scripts\run_all.ps1 -Trials 3 -ArmouryMode Performance     # preliminary (Week 2)
#   .\scripts\run_all.ps1 -Trials 10 -ArmouryMode Performance    # full run (Week 3)
#   .\scripts\run_all.ps1 -Trials 10 -Pin 2 -ArmouryMode Turbo   # pinned + high priority
#
# BEFORE RUNNING: charger plugged in, same Armoury Crate mode every time,
# background apps closed (including OneDrive sync), restart if the laptop has
# been on for days.
#
# Output (nothing is ever overwritten - every run gets a new timestamp):
#   results\run_<timestamp>_t<N>.csv      one row per trial
#   results\run_<timestamp>_t<N>.log      everything bench.exe printed
#   results\run_<timestamp>_t<N>_env.md   environment snapshot for this run
param(
    [int]$Trials = 10,
    [string[]]$Sizes = @('1KB', '16KB', '256KB', '1MB', '64MB', '1GB'),
    [string[]]$Configs = @('aesni_on', 'aesni_off', 'chacha_on', 'chacha_masked_control'),
    [int]$Pin = -1,
    [double]$MinSeconds = 0.5,
    [string]$ArmouryMode = '',
    [switch]$AllowBattery
)

. "$PSScriptRoot\common.ps1"

# The four configurations: which cipher, and whether AES-NI is masked.
$ConfigTable = @{
    aesni_on              = @{ Cipher = 'aes-256-gcm';       Mask = '' }          # BASELINE
    aesni_off             = @{ Cipher = 'aes-256-gcm';       Mask = $AesNiMask }  # COMPARISON 1
    chacha_on             = @{ Cipher = 'chacha20-poly1305'; Mask = '' }          # COMPARISON 2
    chacha_masked_control = @{ Cipher = 'chacha20-poly1305'; Mask = $AesNiMask }  # control: expect no change
}

# ---- Safety checks -------------------------------------------------------------
if (-not (Test-Path $BenchExe)) { throw "bench.exe not found - run scripts\build.ps1 first" }
foreach ($c in $Configs) { if (-not $ConfigTable.ContainsKey($c)) { throw "Unknown config '$c'" } }

Set-Ia32Cap $null
$info = (cmd /c "`"$BenchExe`" --info 2>&1") -join "`n"
if ($info -notmatch 'Build type\s*:\s*Release') { throw "bench.exe is not a Release build:`n$info" }

Add-Type -AssemblyName System.Windows.Forms
$power = [System.Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus
if ($power -ne 'Online' -and -not $AllowBattery) {
    throw "Laptop is not plugged in (power: $power). Plug in the charger, or pass -AllowBattery."
}
if (-not $ArmouryMode) { Write-Warning "Armoury Crate mode not recorded - pass -ArmouryMode <mode>" }

# ---- Output files ----------------------------------------------------------------
New-Item -ItemType Directory -Force $ResultsDir | Out-Null
$stem = Join-Path $ResultsDir ("run_{0}_t{1}" -f (Get-Timestamp), $Trials)
$csv = "$stem.csv"; $log = "$stem.log"
if (Test-Path $csv) { throw "$csv already exists - refusing to overwrite" }
& "$PSScriptRoot\collect_env.ps1" -OutFile "${stem}_env.md" -ArmouryMode $ArmouryMode

$started = Get-Date
$failed = @()
Write-Host "Writing $csv"
"run_all.ps1 started $started  trials=$Trials pin=$Pin minSeconds=$MinSeconds armoury=$ArmouryMode" | Set-Content $log

# ---- Main loop -----------------------------------------------------------------
try {
    foreach ($c in $Configs) {
        $cfg = $ConfigTable[$c]
        Set-Ia32Cap $cfg.Mask          # set or fully remove OPENSSL_ia32cap for this config
        foreach ($s in $Sizes) {
            Write-Host "`n--- $c / $s ---"
            $benchArgs = @('--cipher', $cfg.Cipher, '--size', $s, '--trials', $Trials,
                           '--out', $csv, '--label', $c, '--min-seconds', $MinSeconds)
            if ($Pin -ge 0) { $benchArgs += @('--pin', $Pin) }
            # cmd /c merges stderr into stdout as plain text (so failure banners get logged too).
            cmd /c "`"$BenchExe`" $($benchArgs -join ' ') 2>&1" | Tee-Object -FilePath $log -Append
            if ($LASTEXITCODE -ne 0) { $failed += "$c/$s (exit code $LASTEXITCODE)" }
        }
    }
}
finally {
    Set-Ia32Cap $null                  # never leave the mask set in this terminal
}

# ---- Summary ---------------------------------------------------------------------
$elapsed = (Get-Date) - $started
$data = Import-Csv $csv
Write-Host ("`n===== SUMMARY: {0} rows in {1:N1} min =====" -f $data.Count, $elapsed.TotalMinutes)
$summary = $data | Group-Object config_label, size_bytes | ForEach-Object {
    $t = $_.Group | ForEach-Object { [double]$_.throughput_MBps }
    $mean = ($t | Measure-Object -Average).Average
    $sd = if ($t.Count -gt 1) { [math]::Sqrt(($t | ForEach-Object { ($_ - $mean) * ($_ - $mean) } | Measure-Object -Sum).Sum / ($t.Count - 1)) } else { 0 }
    [pscustomobject]@{
        config = $_.Group[0].config_label
        size_bytes = [int64]$_.Group[0].size_bytes
        trials = $t.Count
        mean_MBps = [math]::Round($mean, 1)
        sd_MBps = [math]::Round($sd, 1)
        all_verified = -not ($_.Group | Where-Object { $_.verified -ne 'PASS' })
    }
}
$summary | Sort-Object { [array]::IndexOf($Configs, $_.config) }, size_bytes | Format-Table -AutoSize

$bad = $data | Where-Object { $_.verified -ne 'PASS' }
if ($bad -or $failed) {
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
    Write-Host "!!! $(@($bad).Count) trial(s) FAILED verification; failed runs: $($failed -join ', ')" -ForegroundColor Red
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
    exit 1
}
Write-Host "All $($data.Count) trials verified PASS. Results: $csv"
