# build.ps1 - configure + build the Release x64 programs, then run the
# correctness tests (with and without the AES-NI mask).
#
#   .\scripts\build.ps1            # build and test
#   .\scripts\build.ps1 -SkipTests # build only
#
# The first build takes several minutes because vcpkg compiles OpenSSL from
# source. Later builds reuse vcpkg's binary cache and are fast.
param([switch]$SkipTests)

. "$PSScriptRoot\common.ps1"
Enter-DevShell
Set-Location $RepoRoot

# Make sure no leftover mask from this terminal leaks into the build/tests.
Set-Ia32Cap $null

cmake --preset x64-release
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed" }
cmake --build --preset x64-release
if ($LASTEXITCODE -ne 0) { throw "Build failed" }

if (-not $SkipTests) {
    ctest --preset x64-release
    if ($LASTEXITCODE -ne 0) { throw "Correctness tests FAILED - do not benchmark until fixed" }
}
Write-Host "Build OK: $BenchExe"
