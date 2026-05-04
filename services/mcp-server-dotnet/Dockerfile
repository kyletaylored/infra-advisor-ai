ARG DD_GIT_REPOSITORY_URL
ARG DD_GIT_COMMIT_SHA

# ── Stage 1: build ─────────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY InfraAdvisor.McpServer.csproj ./
RUN dotnet restore
COPY . .
RUN dotnet publish -c Release -o /app/publish --no-restore

# ── Stage 2: runtime ───────────────────────────────────────────────────────────
FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS runtime
WORKDIR /app

ARG DD_GIT_REPOSITORY_URL
ARG DD_GIT_COMMIT_SHA
ENV DD_GIT_REPOSITORY_URL=${DD_GIT_REPOSITORY_URL}
ENV DD_GIT_COMMIT_SHA=${DD_GIT_COMMIT_SHA}

# Service identification
ENV DD_SERVICE=infratools-mcp-dotnet
ENV ASPNETCORE_URLS=http://+:8000

EXPOSE 8000

COPY --from=build /app/publish .
ENTRYPOINT ["dotnet", "InfraAdvisor.McpServer.dll"]
