# run_all.ps1 - run every configuration x every data size x N trials.
#
#   .\scripts\run_all.ps1 -Trials 3 -ArmouryMode Performance     # quick check
#   .\scripts\run_all.ps1 -Trials 10 -ArmouryMode Performance    # full run (Week 3)
#   .\scripts\run_all.ps1 -Trials 10 -Pin 2 -ArmouryMode Turbo   # pinned + high priority
#
# FAIR ORDER (interleaving): the run is split into N "rounds". Each round
# measures every size once for every configuration, the configurations
# directly after each other:
#     round 1:  1KB: aesni_on, aesni_off, chacha_on, chacha_masked_control
#               16KB: aesni_on, aesni_off, ...   ...   1GB: ...
#     round 2:  same, but the configuration order is ROTATED by one
#               (aesni_off, chacha_on, chacha_masked_control, aesni_on), ...
# So if the laptop slowly heats up or its turbo boost fades during the run,
# every configuration is affected equally, instead of the configuration that
# happened to run last getting all the slow minutes. Rotating the order means
# no configuration is always first (coolest) or last (hottest) within a round.
# Between rounds the script pauses (-CooldownSeconds) to let the CPU cool.
# Each trial is a separate run of bench.exe (with its own warm-up).
#
# BEFORE RUNNING: charger plugged in, same Armoury Crate mode every time,
# background apps closed (including OneDrive sync), restart if the laptop has
# been on for days.
#
# Output (nothing is ever overwritten - every run gets a new timestamp):
#   results\run_<timestamp>_t<N>.csv       one row per trial (with start/end time of each)
#   results\run_<timestamp>_t<N>.log       everything bench.exe printed
#   results\run_<timestamp>_t<N>_env.md    environment snapshot for this run
#   results\run_<timestamp>_t<N>_run.json  parameters + start/end time of the whole run
param(
    [int]$Trials = 10,
    [string[]]$Sizes = @('1KB', '16KB', '256KB', '1MB', '64MB', '1GB'),
    [string[]]$Configs = @('aesni_on', 'aesni_off', 'chacha_on', 'chacha_masked_control'),
    [int]$Pin = -1,
    [double]$MinSeconds = 1.0,       # each trial times at least this long (see bench.cpp)
    [int]$CooldownSeconds = 30,      # pause between rounds
    [string]$ArmouryMode = '',
    [string]$OutDir = '',            # default results\ ; use a temp folder for test runs
    [switch]$AllowBattery
)

. "$PSScriptRoot\common.ps1"

# The configurations: which cipher, and which OPENSSL_ia32cap mask ('' = unset).
# The first four are the experiment; the *_fullmask ones are diagnostics that
# use the proposal's original mask string, which also disables AVX2/AVX-512/
# VAES (see common.ps1). Run them with -Configs if wanted.
$ConfigTable = @{
    aesni_on              = @{ Cipher = 'aes-256-gcm';       Mask = '' }          # BASELINE
    aesni_off             = @{ Cipher = 'aes-256-gcm';       Mask = $AesNiMask }  # COMPARISON 1
    chacha_on             = @{ Cipher = 'chacha20-poly1305'; Mask = '' }          # COMPARISON 2
    chacha_masked_control = @{ Cipher = 'chacha20-poly1305'; Mask = $AesNiMask }  # control: expect no change
    aesni_off_fullmask    = @{ Cipher = 'aes-256-gcm';       Mask = $FullMask }   # diagnostic
    chacha_fullmask       = @{ Cipher = 'chacha20-poly1305'; Mask = $FullMask }   # diagnostic
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
if (-not $OutDir) { $OutDir = $ResultsDir }
New-Item -ItemType Directory -Force $OutDir | Out-Null
$stem = Join-Path $OutDir ("run_{0}_t{1}" -f (Get-Timestamp), $Trials)
$csv = "$stem.csv"; $log = "$stem.log"
if (Test-Path $csv) { throw "$csv already exists - refusing to overwrite" }
& "$PSScriptRoot\collect_env.ps1" -OutFile "${stem}_env.md" -ArmouryMode $ArmouryMode

$started = Get-Date
$failed = @()
$runInfo = [ordered]@{
    started = $started.ToString('o'); ended = $null
    trials = $Trials; sizes = $Sizes; configs = $Configs
    order = 'interleaved by round, config order rotated each round'
    pin = $Pin; min_seconds = $MinSeconds; cooldown_seconds = $CooldownSeconds
    armoury_mode = $ArmouryMode; aesni_off_mask = $AesNiMask
}
Write-Host "Writing $csv"
"run_all.ps1 started $($started.ToString('o'))  trials=$Trials pin=$Pin minSeconds=$MinSeconds cooldown=$CooldownSeconds armoury=$ArmouryMode" | Set-Content $log

# ---- Main loop: N rounds, configurations interleaved ---------------------------
try {
    for ($round = 1; $round -le $Trials; $round++) {
        # Rotate the configuration order by one place each round.
        $shift = ($round - 1) % $Configs.Count
        $order = @($Configs[$shift..($Configs.Count - 1)]) + @(if ($shift -gt 0) { $Configs[0..($shift - 1)] })
        Write-Host "`n===== ROUND $round of $Trials  (order: $($order -join ', ')) =====" -ForegroundColor Cyan
        "===== ROUND $round of $Trials started $((Get-Date).ToString('o'))  order: $($order -join ', ')" | Add-Content $log

        foreach ($s in $Sizes) {
            foreach ($c in $order) {
                $cfg = $ConfigTable[$c]
                Set-Ia32Cap $cfg.Mask      # set or fully remove OPENSSL_ia32cap for this config
                Write-Host "--- round $round | $s | $c ---"
                $benchArgs = @('--cipher', $cfg.Cipher, '--size', $s, '--trials', 1, '--first-trial', $round,
                               '--out', $csv, '--label', $c, '--min-seconds', $MinSeconds)
                if ($Pin -ge 0) { $benchArgs += @('--pin', $Pin) }
                # cmd /c merges stderr into stdout as plain text (so failure banners get logged too).
                cmd /c "`"$BenchExe`" $($benchArgs -join ' ') 2>&1" | Tee-Object -FilePath $log -Append
                if ($LASTEXITCODE -ne 0) { $failed += "round $round $c/$s (exit code $LASTEXITCODE)" }
            }
        }
        Set-Ia32Cap $null
        if ($round -lt $Trials -and $CooldownSeconds -gt 0) {
            Write-Host "Cooling down for $CooldownSeconds s..."
            Start-Sleep -Seconds $CooldownSeconds
        }
    }
}
finally {
    Set-Ia32Cap $null                  # never leave the mask set in this terminal
    $runInfo.ended = (Get-Date).ToString('o')
    $runInfo | ConvertTo-Json | Set-Content "${stem}_run.json" -Encoding utf8
    "run_all.ps1 ended $($runInfo.ended)" | Add-Content $log
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
