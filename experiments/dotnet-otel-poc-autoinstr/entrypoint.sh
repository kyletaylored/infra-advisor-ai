#!/bin/sh
# Detect the container's actual CPU arch at runtime and point
# CORECLR_PROFILER_PATH at the matching native profiler binary
# inside the shared /otel-auto/ volume. The hardcoded path in
# docker-compose.yml was brittle because the image variant Docker
# actually pulls depends on host platform + Docker Desktop settings
# + image manifest contents — none of which are stable assumptions.
#
# Maps uname -m → /otel-auto/{linux-x64|linux-arm64}/...
# Falls back gracefully (disables profiler, keeps app running, prints
# a clear warning) if the .so doesn't exist at the expected path.

set -e

ARCH=$(uname -m)
case "$ARCH" in
  x86_64)   PROFILER_DIR=linux-x64 ;;
  aarch64)  PROFILER_DIR=linux-arm64 ;;
  *)        echo "[entrypoint] ERROR: unsupported arch '$ARCH'"; exit 1 ;;
esac

PROFILER_PATH="/otel-auto/${PROFILER_DIR}/OpenTelemetry.AutoInstrumentation.Native.so"

echo "[entrypoint] uname -m = $ARCH"
echo "[entrypoint] computed CORECLR_PROFILER_PATH = $PROFILER_PATH"

if [ -f "$PROFILER_PATH" ]; then
    echo "[entrypoint] ✓ native profiler binary found"
    export CORECLR_PROFILER_PATH="$PROFILER_PATH"
else
    echo "[entrypoint] ✗ native profiler binary NOT found at $PROFILER_PATH"
    echo "[entrypoint]   /otel-auto/ contains:"
    ls -d /otel-auto/linux-* 2>/dev/null | sed 's/^/[entrypoint]     /'
    echo "[entrypoint]   disabling profiler so the app still starts."
    echo "[entrypoint]   auto-instrumentation will be inactive this run."
    unset CORECLR_ENABLE_PROFILING
    unset CORECLR_PROFILER
    unset CORECLR_PROFILER_PATH
    unset DOTNET_STARTUP_HOOKS
fi

exec dotnet InfraAdvisor.OtelPoc.AutoInstr.dll
