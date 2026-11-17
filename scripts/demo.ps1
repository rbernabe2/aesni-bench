# demo.ps1 - live class demo (about 2-3 minutes) of HOW we measure.
#
#   .\scripts\demo.ps1            # pauses between sections (press Enter)
#   .\scripts\demo.ps1 -NoPause   # runs straight through
#
# Sections:
#   1. The switch: what OpenSSL sees with and without OPENSSL_ia32cap
#   2. openssl speed, AES-256-GCM, with and without the mask
#   3. Correctness: known-answer tests pass with the mask too
#   4. Our harness: 16 KB and 64 MB x 3 trials, aesni_on vs aesni_off
#   5. Verification result for every trial + the CSV rows
#   6. A quick graph
#
# Demo output goes to results\demo\ (new timestamped files, never the
# experiment data). Requires a Release build (scripts\build.ps1).
param([switch]$NoPause)

. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
if (-not (Test-Path $BenchExe)) { throw "bench.exe not found - run scripts\build.ps1 first" }
$env:Path = (Join-Path $VcpkgInstalled 'bin') + ';' + $env:Path     # openssl.exe uses the same DLLs as bench.exe
$demoDir = Join-Path $ResultsDir 'demo'
New-Item -ItemType Directory -Force $demoDir | Out-Null
$ts = Get-Timestamp
$csv = Join-Path $demoDir "demo_$ts.csv"

function Section([string]$title, [string[]]$explain) {
    Write-Host ""
    Write-Host ("=" * 78) -ForegroundColor Cyan
    Write-Host "  $title" -ForegroundColor Cyan
    Write-Host ("=" * 78) -ForegroundColor Cyan
    foreach ($line in $explain) { Write-Host "  $line" -ForegroundColor Gray }
    Write-Host ""
}
function Pause-Demo { if (-not $NoPause) { Read-Host "  [Enter] to continue" | Out-Null } }

$demoStart = Get-Date

# ---- 1 ---------------------------------------------------------------------------
Section "1. The switch: OPENSSL_ia32cap" @(
    "OpenSSL asks the CPU which instructions it has (CPUID) when a program starts.",
    "OPENSSL_ia32cap lets us hide some of them. Our 'AES-NI off' mask hides",
    "AES-NI, PCLMULQDQ, VAES and VPCLMULQDQ - and nothing else.",
    "Below: the capability words OpenSSL actually uses (watch the first two numbers).")
Set-Ia32Cap $null
Write-Host "  AES-NI ON : " -NoNewline; (cmd /c "`"$OpenSslExe`" version -a 2>&1") | Where-Object { $_ -match '^CPUINFO' } | ForEach-Object { $_ -replace 'CPUINFO: OPENSSL_ia32cap=', '' }
Set-Ia32Cap $AesNiMask
Write-Host "  AES-NI OFF: " -NoNewline; (cmd /c "`"$OpenSslExe`" version -a 2>&1") | Where-Object { $_ -match '^CPUINFO' } | ForEach-Object { $_ -replace 'CPUINFO: OPENSSL_ia32cap=', '' }
Set-Ia32Cap $null
Pause-Demo

# ---- 2 ---------------------------------------------------------------------------
Section "2. OpenSSL's own benchmark: openssl speed -evp aes-256-gcm (16 KB blocks, 1 s each)" @(
    "Same openssl.exe and same libcrypto DLL that our program uses.")
foreach ($m in @(@{ Name = 'AES-NI ON '; Mask = '' }, @{ Name = 'AES-NI OFF'; Mask = $AesNiMask })) {
    Set-Ia32Cap $m.Mask
    $out = cmd /c "`"$OpenSslExe`" speed -seconds 1 -bytes 16384 -evp aes-256-gcm 2>&1"
    $line = $out | Where-Object { $_ -match '^AES-256-GCM\s' } | Select-Object -Last 1
    $k = [double]([regex]::Match($line, '([\d.]+)k').Groups[1].Value)
    Write-Host ("  {0}: {1,10:N0} MB/s" -f $m.Name, ($k / 1000)) -ForegroundColor Yellow
}
Set-Ia32Cap $null
Pause-Demo

# ---- 3 ---------------------------------------------------------------------------
Section "3. Correctness first: known-answer tests with AES-NI OFF" @(
    "7,875 official NIST test vectors + the RFC 8439 vector + tamper tests.",
    "If these failed, no speed number would mean anything.")
Set-Ia32Cap $AesNiMask
cmd /c "`"$KatExe`" 2>&1" | Where-Object { $_ -match 'passed|PASSED|FAILED' } | ForEach-Object { Write-Host "  $_" }
Set-Ia32Cap $null
Pause-Demo

# ---- 4 ---------------------------------------------------------------------------
Section "4. Our harness (bench.exe): 16 KB and 64 MB, 3 trials each, AES-NI on vs off" @(
    "For each run bench.exe:",
    "  a) fills a buffer with fixed-seed random data and allocates everything up front",
    "  b) warms up for 0.25 s and picks how many encryptions fill each trial",
    "  c) times the loop with std::chrono::steady_clock (wall time)",
    "     and GetProcessTimes (CPU time); new 96-bit nonce for every encryption",
    "  d) decrypts the result and checks it matches + the tag verifies",
    "  e) appends one CSV row per trial")
foreach ($size in '16KB', '64MB') {
    foreach ($cfg in @(@{ Label = 'aesni_on'; Mask = '' }, @{ Label = 'aesni_off'; Mask = $AesNiMask })) {
        Set-Ia32Cap $cfg.Mask
        Write-Host "  --- $($cfg.Label), $size ---" -ForegroundColor Yellow
        cmd /c "`"$BenchExe`" --cipher aes-256-gcm --size $size --trials 3 --min-seconds 0.5 --out `"$csv`" --label $($cfg.Label) 2>&1" |
            Where-Object { $_ -match 'Warm-up|trial|FAILED' } | ForEach-Object { Write-Host "  $_" }
    }
}
Set-Ia32Cap $null
Pause-Demo

# ---- 5 ---------------------------------------------------------------------------
Section "5. Verification + the raw data" @("Every trial decrypted correctly? (verified column)")
$rows = Import-Csv $csv
$rows | Select-Object config_label, size_bytes, trial, iterations, throughput_MBps, latency_us_per_op, cpu_util_pct_one_core, verified |
    Format-Table -AutoSize | Out-String -Width 160 | Write-Host
$failed = @($rows | Where-Object { $_.verified -ne 'PASS' })
if ($failed.Count -eq 0) { Write-Host "  All $($rows.Count) trials verified PASS." -ForegroundColor Green }
else { Write-Host "  !!! $($failed.Count) trial(s) FAILED verification !!!" -ForegroundColor Red }
foreach ($size in ($rows | Select-Object -ExpandProperty size_bytes -Unique)) {
    $on  = ($rows | Where-Object { $_.config_label -eq 'aesni_on'  -and $_.size_bytes -eq $size } | Measure-Object throughput_MBps -Average).Average
    $off = ($rows | Where-Object { $_.config_label -eq 'aesni_off' -and $_.size_bytes -eq $size } | Measure-Object throughput_MBps -Average).Average
    Write-Host ("  {0,9} bytes: AES-NI on {1,8:N0} MB/s, off {2,6:N0} MB/s  ->  {3:N1}x faster with AES-NI" -f $size, $on, $off, ($on / $off)) -ForegroundColor Yellow
}
Pause-Demo

# ---- 6 ---------------------------------------------------------------------------
Section "6. Quick graph" @("analysis\demo_plot.py draws this run's CSV.")
$png = [IO.Path]::ChangeExtension($csv, '.png')
py "$RepoRoot\analysis\demo_plot.py" $csv $png
if (Test-Path $png) { Write-Host "  Saved $png"; Start-Process $png }

Write-Host ("`nDemo finished in {0:N0} s. Data: {1}" -f ((Get-Date) - $demoStart).TotalSeconds, $csv) -ForegroundColor Cyan
