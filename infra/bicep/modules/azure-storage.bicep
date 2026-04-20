// azure-storage.bicep — Azure Blob Storage for InfraAdvisor AI
// Containers:
//   raw-data       — Airflow ingestion output (NBI, FEMA, EIA, EPA JSON/Parquet)
//   processed-data — Spark feature engineering output (chunked, embedding-ready)
//   knowledge-docs — Synthetic + real documents for AI Search knowledge base
// Used by Datadog Storage Monitoring for blob-level observability.

@description('Azure region for the storage account')
param location string

@description('Environment tag value (e.g. dev, staging, prod)')
param environment string

// Storage account name: 3-24 chars, lowercase + numbers only
var accountName = 'stinfraadv${environment}'

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: accountName
  location: location
  tags: {
    environment: environment
    project: 'infra-advisor-ai'
    managedBy: 'bicep'
  }
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-01-01' = {
  parent: storageAccount
  name: 'default'
}

resource rawDataContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'raw-data'
  properties: { publicAccess: 'None' }
}

resource processedDataContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'processed-data'
  properties: { publicAccess: 'None' }
}

resource knowledgeDocsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-01-01' = {
  parent: blobService
  name: 'knowledge-docs'
  properties: { publicAccess: 'None' }
}

@description('Storage account name')
output storageAccountName string = storageAccount.name

@description('Primary blob service endpoint')
output blobEndpoint string = storageAccount.properties.primaryEndpoints.blob

@description('Resource ID of the storage account')
output resourceId string = storageAccount.id
