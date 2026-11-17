# size_order_check.ps1 - DIAGNOSTIC: is the drop at 64 MB / 1 GB a cache /
# memory effect, or an artifact of always running the large sizes last?
#
# In run_all.ps1 every round goes 1KB -> 16KB -> ... -> 1GB, so the large
# sizes always run after the CPU has been busy the longest (hotter, lower
# turbo clock). Size and "time under load" are mixed up. This script breaks
# that link:
#   * every (size, config) pair runs once per round, in a SHUFFLED order
#     (fixed seed, saved in the _run.json file)
#   * a cooldown pause before EVERY trial, so each starts from a similar
#     temperature
#   * Windows' "% Processor Performance" counter (effective clock as % of the
#     4.0 GHz base, per logical processor) is logged every second, so each
#     trial's clock speed can be checked afterwards
#
#   .\scripts\size_order_check.ps1                      # 5 rounds, defaults
#
# Output: results\size_check_<timestamp>.csv (+ _perf.csv, _run.json, .log)
param(
    [int]$Rounds = 5,
    [string[]]$Sizes = @('256KB', '64MB', '1GB'),
    [string[]]$Configs = @('aesni_on', 'aesni_off', 'chacha_on'),
    [int]$CooldownSeconds = 10,
    [double]$MinSeconds = 1.0,
    [int]$Seed = 20261117,
    [string]$OutDir = ''
)

. "$PSScriptRoot\common.ps1"
$ConfigTable = @{
    aesni_on  = @{ Cipher = 'aes-256-gcm';       Mask = '' }
    aesni_off = @{ Cipher = 'aes-256-gcm';       Mask = $AesNiMask }
    chacha_on = @{ Cipher = 'chacha20-poly1305'; Mask = '' }
}
if (-not (Test-Path $BenchExe)) { throw "bench.exe not found - run scripts\build.ps1 first" }
Add-Type -AssemblyName System.Windows.Forms
if ([System.Windows.Forms.SystemInformation]::PowerStatus.PowerLineStatus -ne 'Online') { throw "Plug in the charger." }

if (-not $OutDir) { $OutDir = $ResultsDir }
$stem = Join-Path $OutDir ("size_check_{0}" -f (Get-Timestamp))
$csv = "$stem.csv"; $log = "$stem.log"; $perf = "${stem}_perf.csv"

# Start logging the per-processor clock counter in the background (1 sample/s).
$typeperf = Start-Process typeperf -ArgumentList @('"\Processor Information(*)\% Processor Performance"',
    '-si', '1', '-f', 'CSV', '-o', "`"$perf`"", '-y') -WindowStyle Hidden -PassThru

$rng = New-Object System.Random($Seed)
$started = Get-Date
$orders = @()
try {
    for ($round = 1; $round -le $Rounds; $round++) {
        # All (size, config) pairs, shuffled (Fisher-Yates with the fixed seed).
        $pairs = @(foreach ($s in $Sizes) { foreach ($c in $Configs) { "$s|$c" } })
        for ($i = $pairs.Count - 1; $i -gt 0; $i--) {
            $j = $rng.Next($i + 1); $tmp = $pairs[$i]; $pairs[$i] = $pairs[$j]; $pairs[$j] = $tmp
        }
        $orders += ,@($pairs)
        Write-Host "`n===== ROUND $round of ${Rounds}: $($pairs -join ', ') =====" -ForegroundColor Cyan
        "===== ROUND $round $((Get-Date).ToString('o')) order: $($pairs -join ', ')" | Add-Content $log
        foreach ($p in $pairs) {
            $s, $c = $p -split '\|'
            Start-Sleep -Seconds $CooldownSeconds
            Set-Ia32Cap $ConfigTable[$c].Mask
            Write-Host "--- round $round | $s | $c ---"
            cmd /c "`"$BenchExe`" --cipher $($ConfigTable[$c].Cipher) --size $s --trials 1 --first-trial $round --out `"$csv`" --label $c --min-seconds $MinSeconds 2>&1" |
                Tee-Object -FilePath $log -Append | Select-String 'trial' | ForEach-Object { Write-Host "  $_" }
            Set-Ia32Cap $null
        }
    }
}
finally {
    Set-Ia32Cap $null
    Start-Sleep -Seconds 2
    if ($typeperf -and -not $typeperf.HasExited) { Stop-Process -Id $typeperf.Id -Force }
    [ordered]@{ started = $started.ToString('o'); ended = (Get-Date).ToString('o'); rounds = $Rounds
                sizes = $Sizes; configs = $Configs; cooldown_seconds = $CooldownSeconds
                min_seconds = $MinSeconds; seed = $Seed; orders = $orders } |
        ConvertTo-Json -Depth 4 | Set-Content "${stem}_run.json" -Encoding utf8
}
$rows = Import-Csv $csv
Write-Host "`n$($rows.Count) trials, $(@($rows | Where-Object verified -ne 'PASS').Count) failed verification. Data: $csv"
