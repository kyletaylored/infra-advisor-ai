// azure-ai-search.bicep — Azure AI Search for InfraAdvisor AI
// SKU: standard (supports hybrid search and semantic ranker)
// Semantic search tier: free (up to 1,000 semantic queries/month at no charge)
// 1 replica, 1 partition — right-sized for dev/demo lab

@description('Azure region for the search service')
param location string

@description('Environment tag value (e.g. dev, staging, prod)')
param environment string

var searchServiceName = 'srch-infra-advisor-${environment}'

resource searchService 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchServiceName
  location: location
  tags: {
    environment: environment
    project: 'infra-advisor-ai'
  }
  sku: {
    name: 'standard'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: 'free'
    publicNetworkAccess: 'enabled'
  }
}

@description('Azure AI Search service name')
output searchName string = searchService.name

@description('Azure AI Search HTTPS endpoint')
output endpoint string = 'https://${searchService.name}.search.windows.net'
