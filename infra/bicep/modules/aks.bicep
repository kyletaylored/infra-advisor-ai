// aks.bicep — AKS cluster for InfraAdvisor AI
// 3-node system pool, Standard_D2s_v3, Kubernetes 1.33
// Workload Identity + OIDC issuer enabled for pod-level Azure auth
// Azure RBAC enabled, Azure CNI networking

@description('Azure region for the AKS cluster')
param location string

@description('Environment tag value (e.g. dev, staging, prod)')
param environment string

@description('Number of nodes in the system node pool')
param nodeCount int = 3

@description('VM size for each node')
param nodeVmSize string = 'Standard_D2s_v3'

var clusterName = 'aks-infra-advisor-${environment}'
var dnsPrefix = 'infra-advisor-${environment}'
// Explicit node RG name avoids the auto-generated MC_... prefix.
// NOTE: nodeResourceGroup is immutable after cluster creation.
// The live cluster was created before this was set, so its node RG is
// MC_rg-tola-infra-advisor-ai_aks-infra-advisor-dev_eastus.
// Recreating the cluster will use this clean name.
var nodeResourceGroupName = 'rg-tola-infra-advisor-ai-nodes'

resource aksCluster 'Microsoft.ContainerService/managedClusters@2024-02-01' = {
  name: clusterName
  location: location
  tags: {
    environment: environment
    project: 'infra-advisor-ai'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    kubernetesVersion: '1.33'
    dnsPrefix: dnsPrefix
    nodeResourceGroup: nodeResourceGroupName
    enableRBAC: true
    aadProfile: {
      managed: true
      enableAzureRBAC: true
    }
    oidcIssuerProfile: {
      enabled: true
    }
    securityProfile: {
      workloadIdentity: {
        enabled: true
      }
    }
    networkProfile: {
      networkPlugin: 'azure'
      loadBalancerSku: 'standard'
    }
    agentPoolProfiles: [
      {
        name: 'systempool'
        count: nodeCount
        vmSize: nodeVmSize
        mode: 'System'
        osType: 'Linux'
        osSKU: 'Ubuntu'
        enableAutoScaling: false
        type: 'VirtualMachineScaleSets'
      }
    ]
  }
}

@description('Name of the AKS cluster')
output aksName string = aksCluster.name

@description('Resource ID of the AKS cluster')
output aksId string = aksCluster.id

@description('Fully-qualified domain name of the AKS API server')
output aksFqdn string = aksCluster.properties.fqdn

@description('OIDC issuer URL for workload identity federation')
output oidcIssuerUrl string = aksCluster.properties.oidcIssuerProfile.issuerURL

@description('Name of the AKS-managed node resource group')
output nodeResourceGroup string = aksCluster.properties.nodeResourceGroup
