# syntax=docker/dockerfile:1
# Multi-stage build for the auto-instrumented POC.
#
# Profiler attach is configured entirely via env vars in docker-compose.yml
# (the canonical K8s operator pattern). Init container populates /otel-auto/;
# this image just runs the .NET app. No entrypoint shim, no sourcing of
# instrument.sh — eliminates dash/bash env-export surprises.

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY *.csproj ./
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0
WORKDIR /app
COPY --from=build /app ./
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
EXPOSE 8080
ENV ASPNETCORE_URLS=http://+:8080
# entrypoint.sh detects arch via `uname -m`, computes the right
# CORECLR_PROFILER_PATH (linux-x64 or linux-arm64), verifies the .so
# exists, logs the choice, then exec-dotnet's the app. Hardcoded
# PROFILER_PATH in compose was fragile — the image variant Docker
# actually pulls depends on host + image manifest layout, so we
# detect rather than assume.
ENTRYPOINT ["/app/entrypoint.sh"]
