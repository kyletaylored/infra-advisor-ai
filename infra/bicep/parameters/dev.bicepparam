// dev.bicepparam — development environment parameters for InfraAdvisor AI
//
// Usage:
//   az deployment sub create \
//     --location eastus \
//     --template-file infra/bicep/main.bicep \
//     --parameters infra/bicep/parameters/dev.bicepparam

using '../main.bicep'

param location = 'eastus'
param environment = 'dev'
param aksNodeCount = 3
param aksNodeVmSize = 'Standard_D2s_v3'
