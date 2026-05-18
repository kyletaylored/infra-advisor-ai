ARG DD_GIT_REPOSITORY_URL
ARG DD_GIT_COMMIT_SHA

# ── Stage 1: build ─────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/sdk:10.0-resolute AS build
WORKDIR /src
COPY InfraAdvisor.AgentApi.csproj ./
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /app/publish --no-restore

# ── Stage 2: runtime ───────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/aspnet:10.0-resolute AS runtime
WORKDIR /app

ARG DD_GIT_REPOSITORY_URL
ARG DD_GIT_COMMIT_SHA
ENV DD_GIT_REPOSITORY_URL=${DD_GIT_REPOSITORY_URL}
ENV DD_GIT_COMMIT_SHA=${DD_GIT_COMMIT_SHA}
ENV ASPNETCORE_URLS=http://+:8001

EXPOSE 8001

COPY --from=build /app/publish .

# aspnet base image ships a non-root 'app' user (UID=1654). Use it so
# K8s securityContext: runAsNonRoot: true passes without a fixed UID.
USER app

ENTRYPOINT ["dotnet", "InfraAdvisor.AgentApi.dll"]
