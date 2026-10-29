cmake_minimum_required(VERSION 3.21)

# vcpkg (manifest mode) is hooked in through CMAKE_TOOLCHAIN_FILE in
# CMakePresets.json. It reads vcpkg.json and builds OpenSSL for us.
project(aesni_bench LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(OpenSSL REQUIRED)

if(MSVC)
    # /W4 = lots of warnings, /permissive- = strict standard C++.
    # Release builds already get /O2 (optimize for speed) from CMake.
    add_compile_options(/W4 /permissive- /utf-8)
    add_compile_definitions(WIN32_LEAN_AND_MEAN NOMINMAX)
endif()

# ---- Benchmark harness -------------------------------------------------------
add_executable(bench src/bench.cpp)
target_link_libraries(bench PRIVATE OpenSSL::Crypto psapi)
# Bake the build type into the program so it can refuse to benchmark a Debug
# build by accident. With the Ninja generator $<CONFIG> == CMAKE_BUILD_TYPE.
target_compile_definitions(bench PRIVATE BENCH_BUILD_TYPE="$<CONFIG>")

# ---- Known-answer tests ------------------------------------------------------
add_executable(kat src/kat.cpp)
target_link_libraries(kat PRIVATE OpenSSL::Crypto)
target_compile_definitions(kat PRIVATE
    KAT_VECTOR_DIR="${CMAKE_SOURCE_DIR}/testvectors")

# `ctest` runs the known-answer tests twice: once normally (hardware AES-NI
# allowed) and once with AES-NI + PCLMULQDQ masked off, so both the hardware
# and the pure-software code paths inside OpenSSL are proven correct.
enable_testing()
add_test(NAME kat_default COMMAND kat)
add_test(NAME kat_aesni_masked COMMAND kat)
set_tests_properties(kat_aesni_masked PROPERTIES
    ENVIRONMENT "OPENSSL_ia32cap=~0x200000200000000")
