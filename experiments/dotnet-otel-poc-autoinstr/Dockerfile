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
EXPOSE 8080
ENV ASPNETCORE_URLS=http://+:8080
ENTRYPOINT ["dotnet", "InfraAdvisor.OtelPoc.AutoInstr.dll"]
