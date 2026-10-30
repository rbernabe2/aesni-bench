# openssl_speed.ps1 - cross-check with OpenSSL's own benchmark tool.
#
#   .\scripts\openssl_speed.ps1             # 3 seconds per block size
#   .\scripts\openssl_speed.ps1 -Seconds 1  # quicker
#
# Purpose:
#  1. Day-1 proof that the AES-NI switch works: AES-256-GCM must get much
#     slower with OPENSSL_ia32cap set, and ChaCha20-Poly1305 must not change.
#  2. A reference to compare our own harness's numbers against.
#
# It uses the openssl.exe that vcpkg built in the SAME build as bench.exe, and
# puts that build's bin folder first on PATH, so both tools load the exact
# same libcrypto DLL (the script checks this by comparing file hashes).
#
# Two modes of `openssl speed` are run:
#   -evp   : streams data through one context (no new nonce/tag per block)
#   -aead  : per block: new nonce, AAD, encrypt, tag - the same sequence of
#            steps our harness times (only if this OpenSSL version supports it)
#
# openssl speed reports "1000s of bytes per second"; we convert to MB/s with
# 1 MB = 1,000,000 bytes, the same unit bench.exe uses.
param([int]$Seconds = 3)

. "$PSScriptRoot\common.ps1"
if (-not (Test-Path $OpenSslExe)) { throw "openssl.exe not found at $OpenSslExe - run scripts\build.ps1 first" }

# Same DLLs as bench.exe: put vcpkg's bin folder first on PATH.
$vcpkgBin = Join-Path $VcpkgInstalled 'bin'
$env:Path = "$vcpkgBin;$env:Path"
$dllVcpkg = Get-ChildItem $vcpkgBin -Filter 'libcrypto-*.dll' | Select-Object -First 1
$dllBench = Join-Path $BuildDir $dllVcpkg.Name
$sameDll = (Test-Path $dllBench) -and
    ((Get-FileHash $dllVcpkg.FullName).Hash -eq (Get-FileHash $dllBench).Hash)

$ts = Get-Timestamp
New-Item -ItemType Directory -Force $ResultsDir | Out-Null
$version = (cmd /c "`"$OpenSslExe`" version 2>&1") -join ' '
$supportsAead = ((cmd /c "`"$OpenSslExe`" speed -help 2>&1") -join "`n") -match '-aead'

Write-Host "openssl.exe : $OpenSslExe"
Write-Host "version     : $version"
Write-Host "same libcrypto DLL as bench.exe: $sameDll ($($dllVcpkg.Name))"
Write-Host "-aead mode supported: $supportsAead"

# Each run: a name, the cipher, the OPENSSL_ia32cap value ('' = unset), extra args.
# "vaes_masked" is a diagnostic: it hides only VAES + VPCLMULQDQ (the newer
# wide vector AES instructions, stored in OpenSSL's second capability word)
# while leaving classic AES-NI visible. "~0x0" for the first word = no change.
$VaesMask = '~0x0:~0x60000000000'
$runs = @(
    @{ Name = 'aes-256-gcm_on';           Cipher = 'aes-256-gcm';       Mask = '';         Mode = 'evp' }
    @{ Name = 'aes-256-gcm_masked';       Cipher = 'aes-256-gcm';       Mask = $AesNiMask; Mode = 'evp' }
    @{ Name = 'chacha20-poly1305_on';     Cipher = 'chacha20-poly1305'; Mask = '';         Mode = 'evp' }
    @{ Name = 'chacha20-poly1305_masked'; Cipher = 'chacha20-poly1305'; Mask = $AesNiMask; Mode = 'evp' }
    @{ Name = 'aes-256-gcm_vaes_masked';  Cipher = 'aes-256-gcm';       Mask = $VaesMask;  Mode = 'evp' }
)
if ($supportsAead) {
    $runs += @(
        @{ Name = 'aes-256-gcm_on_aead';           Cipher = 'aes-256-gcm';       Mask = '';         Mode = 'aead' }
        @{ Name = 'aes-256-gcm_masked_aead';       Cipher = 'aes-256-gcm';       Mask = $AesNiMask; Mode = 'aead' }
        @{ Name = 'chacha20-poly1305_on_aead';     Cipher = 'chacha20-poly1305'; Mask = '';         Mode = 'aead' }
        @{ Name = 'chacha20-poly1305_masked_aead'; Cipher = 'chacha20-poly1305'; Mask = $AesNiMask; Mode = 'aead' }
    )
}

$rows = @()
try {
    foreach ($r in $runs) {
        Set-Ia32Cap $r.Mask
        $args_ = @('speed', '-seconds', $Seconds, '-evp', $r.Cipher)
        if ($r.Mode -eq 'aead') { $args_ += '-aead' }
        Write-Host "`n=== $($r.Name)   (OPENSSL_ia32cap=$(if ($r.Mask) { $r.Mask } else { 'unset' })) ==="
        # CPUINFO = the capability bits OpenSSL is really using after applying
        # the mask. With the AES-NI mask, bit 57 (AES-NI) and bit 33 (PCLMULQDQ)
        # of the first number must be 0 - direct proof the switch took effect.
        $cpuinfo = (cmd /c "`"$OpenSslExe`" version -a 2>&1") | Where-Object { $_ -match '^CPUINFO' }
        Write-Host $cpuinfo
        $out = (cmd /c "`"$OpenSslExe`" $($args_ -join ' ') 2>&1")
        $file = Join-Path $ResultsDir "openssl_speed_${ts}_$($r.Name).txt"
        @("# command: openssl $($args_ -join ' ')", "# OPENSSL_ia32cap: $(if ($r.Mask) { $r.Mask } else { 'unset' })",
          "# $version", "# $cpuinfo") + $out |
            Set-Content -Path $file -Encoding utf8

        # Find the results table: a "type  16 bytes  64 bytes ..." header, then one data line.
        $hdrIdx = -1
        for ($i = 0; $i -lt $out.Count; $i++) { if ($out[$i] -match '^type\s+\d+ bytes') { $hdrIdx = $i } }
        if ($hdrIdx -lt 0) { Write-Warning "Could not parse output of $($r.Name) - see $file"; continue }
        $sizes = [regex]::Matches($out[$hdrIdx], '(\d+) bytes') | ForEach-Object { [int]$_.Groups[1].Value }
        $values = [regex]::Matches($out[$hdrIdx + 1], '([\d.]+)k') | ForEach-Object { [double]$_.Groups[1].Value }
        for ($j = 0; $j -lt [math]::Min($sizes.Count, $values.Count); $j++) {
            $rows += [pscustomobject]@{
                run = $r.Name; mode = $r.Mode; cipher = $r.Cipher
                openssl_ia32cap = $(if ($r.Mask) { $r.Mask } else { 'unset' })
                block_bytes = $sizes[$j]
                throughput_MBps = [math]::Round($values[$j] / 1000.0, 2)   # 1000s of B/s -> MB/s
            }
        }
        Write-Host ($out[$hdrIdx]); Write-Host ($out[$hdrIdx + 1])
    }
}
finally {
    Set-Ia32Cap $null     # never leave the mask set in this terminal
}

$csv = Join-Path $ResultsDir "openssl_speed_${ts}.csv"
$rows | Export-Csv -Path $csv -NoTypeInformation -Encoding utf8
Write-Host "`nSaved raw outputs and $csv"

# Quick verdict on the AES-NI switch, using the largest block size.
$big = ($rows | Measure-Object block_bytes -Maximum).Maximum
function Get-Mbps($run) { ($rows | Where-Object { $_.run -eq $run -and $_.block_bytes -eq $big }).throughput_MBps }
$on = Get-Mbps 'aes-256-gcm_on'; $off = Get-Mbps 'aes-256-gcm_masked'
$con = Get-Mbps 'chacha20-poly1305_on'; $coff = Get-Mbps 'chacha20-poly1305_masked'
if ($on -and $off) {
    Write-Host ("AES-256-GCM  @ {0} B: unmasked {1} MB/s, masked {2} MB/s -> {3:N1}x" -f $big, $on, $off, ($on / $off))
}
if ($con -and $coff) {
    Write-Host ("ChaCha20-P1305 @ {0} B: unmasked {1} MB/s, masked {2} MB/s -> {3:N2}x" -f $big, $con, $coff, ($con / $coff))
}
