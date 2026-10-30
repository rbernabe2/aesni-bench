# common.ps1 - shared helpers, dot-sourced by the other scripts:  . "$PSScriptRoot\common.ps1"
#
# Why this exists: on Windows the C++ compiler (cl), CMake, Ninja and vcpkg that
# come with Visual Studio are NOT on PATH in a normal terminal. Visual Studio
# provides a "Developer shell" that adds them. Enter-DevShell finds Visual
# Studio with vswhere and loads that shell into the current PowerShell session.

$ErrorActionPreference = 'Stop'

# Also search the PATH saved in Windows settings, so tools installed after this
# terminal was opened (e.g. Git) are still found. Appended, so it never
# changes which program wins when one is already on PATH.
$env:Path = $env:Path + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine') +
            ';' + [Environment]::GetEnvironmentVariable('Path', 'User')

$RepoRoot   = Split-Path -Parent $PSScriptRoot
# Build output lives OUTSIDE the repo, in %LOCALAPPDATA%, because the repo is
# in OneDrive: syncing hundreds of MB of OpenSSL build files would slow
# everything down and OneDrive can lock files in the middle of a build.
$BuildDir   = Join-Path $env:LOCALAPPDATA 'aesni-bench\build\x64-release'   # Release x64 only
$BenchExe   = Join-Path $BuildDir 'bench.exe'
$KatExe     = Join-Path $BuildDir 'kat.exe'
# vcpkg installs OpenSSL here: the libraries, and (because vcpkg.json asks for
# the "tools" feature) the openssl.exe command-line program.
$VcpkgInstalled = Join-Path $BuildDir 'vcpkg_installed\x64-windows'
$OpenSslExe = Join-Path $VcpkgInstalled 'tools\openssl\openssl.exe'
$ResultsDir = Join-Path $RepoRoot 'results'

# Masks for OpenSSL's CPU-feature detection. "~X" means "clear the bits in X".
# OpenSSL keeps several 64-bit capability words, written separated by ':'.
#
# $AesNiMask hides ALL hardware AES help and nothing else:
#   word 1: AES-NI (bit 57) + PCLMULQDQ (bit 33)        -> ~0x200000200000000
#   word 2: VAES (bit 41) + VPCLMULQDQ (bit 42)          -> ~0x60000000000
#   word 3: unchanged                                    -> ~0x0
# Two things we found with `openssl version -a` (CPUINFO line) + openssl speed:
#  * OpenSSL 3.6.5 sets any word NOT given in the variable to ZERO. Word 2 also
#    holds AVX2, AVX-512, SHA, BMI etc., so it must be listed explicitly.
#  * On Zen 4, OpenSSL's fastest AES-GCM code uses VAES/VPCLMULQDQ (AVX-512)
#    and checks only the word-2 bits. Hiding just AES-NI + PCLMULQDQ left
#    AES-GCM exactly as fast, so VAES + VPCLMULQDQ must be hidden too.
$AesNiMask = '~0x200000200000000:~0x60000000000:~0x0'
# The original mask from our proposal. Because words 2-3 are omitted it ALSO
# disables AVX2/AVX-512/VAES etc. Kept only for the *_fullmask diagnostic configs.
$FullMask = '~0x200000200000000'

function Enter-DevShell {
    # We use a standalone vcpkg (VCPKG_ROOT=C:\vcpkg, set for the Windows user),
    # NOT the copy bundled with Visual Studio 2022: that copy (April 2025) tries
    # to download an MSYS2 package that no longer exists, so OpenSSL fails to
    # build. The VS dev shell overwrites VCPKG_ROOT with its bundled copy, so we
    # remember the user's setting and put it back afterwards.
    $userVcpkg = [Environment]::GetEnvironmentVariable('VCPKG_ROOT', 'User')
    if (-not $userVcpkg -or -not (Test-Path (Join-Path $userVcpkg 'vcpkg.exe'))) {
        throw "Standalone vcpkg not found. Install it (see README) and set the user environment variable VCPKG_ROOT."
    }
    if (Get-Command cl -ErrorAction SilentlyContinue) {             # already loaded
        $env:VCPKG_ROOT = $userVcpkg
        return
    }
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) { throw "vswhere.exe not found - is Visual Studio installed?" }
    $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $vsPath) { throw "No Visual Studio with the C++ x64 tools was found." }
    # vswhere's folder is put on PATH first because the dev-shell script calls it.
    $env:Path = (Split-Path $vswhere) + ';' + $env:Path
    & (Join-Path $vsPath 'Common7\Tools\Launch-VsDevShell.ps1') -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
    $env:VCPKG_ROOT = $userVcpkg
    $env:Path = "$userVcpkg;$env:Path"
}

# Make sure OPENSSL_ia32cap is either set to $Value or completely removed.
# (Setting it to an empty string is NOT the same as unset, so we remove it.)
function Set-Ia32Cap([string]$Value) {
    if ([string]::IsNullOrEmpty($Value)) {
        Remove-Item Env:\OPENSSL_ia32cap -ErrorAction SilentlyContinue
    } else {
        $env:OPENSSL_ia32cap = $Value
    }
}

function Get-Timestamp { (Get-Date).ToString('yyyyMMdd_HHmmss') }
