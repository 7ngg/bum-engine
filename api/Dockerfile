# Orchestrator API. Build context = repo root.
#   docker build -f api/Dockerfile -t bum-api .
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY api/Api.csproj api/
RUN dotnet restore api/Api.csproj
COPY api/ api/
RUN dotnet publish api/Api.csproj -c Release -o /publish

FROM mcr.microsoft.com/dotnet/aspnet:10.0 AS runtime
WORKDIR /app
COPY --from=build /publish .
ENV ASPNETCORE_URLS=http://0.0.0.0:8080 \
    ConnectionStrings__Default="Data Source=/data/bumengine.db" \
    Export__HandoffDir=/data/handoff \
    Export__OutputDir=/data/output
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["dotnet", "BumEngine.Api.dll"]
