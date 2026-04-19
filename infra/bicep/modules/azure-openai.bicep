// azure-openai.bicep — Azure OpenAI account for InfraAdvisor AI
// SKU: S0 (standard pay-as-you-go)
// Model deployments:
//   - gpt-4o              → primary reasoning and synthesis model (LangChain ReAct agent)
//   - text-embedding-ada-002 → embedding model for Azure AI Search vector indexing

@description('Azure region for the OpenAI account')
param location string

@description('Environment tag value (e.g. dev, staging, prod)')
param environment string

var openAiAccountName = 'oai-infra-advisor-${environment}'

resource openAiAccount 'Microsoft.CognitiveServices/accounts@2024-04-01-preview' = {
  name: openAiAccountName
  location: location
  tags: {
    environment: environment
    project: 'infra-advisor-ai'
  }
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: openAiAccountName
    publicNetworkAccess: 'Enabled'
    restore: false
  }
}

// GPT-4o — primary agent LLM
// capacity unit = thousands of tokens per minute (TPM)
resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openAiAccount
  name: 'gpt-4o'
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-11-20'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

// text-embedding-ada-002 — vector embedding for RAG pipeline
// capacity unit = thousands of tokens per minute (TPM)
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-04-01-preview' = {
  parent: openAiAccount
  name: 'text-embedding-ada-002'
  dependsOn: [
    gpt4oDeployment
  ]
  sku: {
    name: 'Standard'
    capacity: 10
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-ada-002'
      version: '2'
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

@description('Azure OpenAI account name')
output openAiName string = openAiAccount.name

@description('Azure OpenAI HTTPS endpoint')
output endpoint string = openAiAccount.properties.endpoint
