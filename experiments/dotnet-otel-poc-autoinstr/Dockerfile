# syntax=docker/dockerfile:1
# Multi-stage build for the auto-instrumented POC.
#
# At runtime, an init container (defined in docker-compose.yml) copies the
# OTel .NET auto-instrumentation distro into a shared volume at /otel-auto/.
# We source /otel-auto/instrument.sh as the entrypoint shim, which sets the
# CORECLR_* env vars for the right CPU arch (linux-arm64 on Apple Silicon,
# linux-x64 elsewhere) before launching dotnet.

FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY *.csproj ./
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/aspnet:10.0
WORKDIR /app
COPY --from=build /app ./
EXPOSE 8080
ENV ASPNETCORE_URLS=http://+:8080 \
    OTEL_DOTNET_AUTO_HOME=/otel-auto

# instrument.sh exports CORECLR_ENABLE_PROFILING, CORECLR_PROFILER,
# CORECLR_PROFILER_PATH, DOTNET_STARTUP_HOOKS, DOTNET_ADDITIONAL_DEPS,
# DOTNET_SHARED_STORE — all the bits the .NET host needs to attach the
# profiler at process startup. Then we exec the app.
ENTRYPOINT ["sh", "-c", ". /otel-auto/instrument.sh && exec dotnet InfraAdvisor.OtelPoc.AutoInstr.dll"]
