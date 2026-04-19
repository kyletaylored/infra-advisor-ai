// monitoring.bicep — Monitoring infrastructure for InfraAdvisor AI
//
// DATADOG (primary observability platform):
// NOTE: The Datadog agent is NOT deployed as an Azure resource.
// Per architecture decision (PRD § 4), Datadog runs as a DaemonSet +
// ClusterAgent via Helm on AKS in the `datadog` namespace.
//
// Deployment approach:
//   helm repo add datadog https://helm.datadoghq.com
//   helm upgrade --install datadog-agent datadog/datadog \
//     --namespace datadog --create-namespace \
//     --values k8s/datadog/values.yaml \
//     --set datadog.apiKey=$DD_API_KEY \
//     --set datadog.site=datadoghq.com
//
// The Helm values in k8s/datadog/values.yaml configure:
//   - DaemonSet on every AKS node (infrastructure + container monitoring)
//   - ClusterAgent for Kubernetes state metrics and HPA support
//   - APM (port 8126) and DogStatsD (port 8125) listeners
//   - Kafka JMX integration via autodiscovery annotations on broker pods
//   - Redis integration via autodiscovery annotations on the Redis pod
//   - Airflow integration via Data Jobs Monitoring (DJM)
//   - Log collection from all namespaces
//   - LLM Observability for agent-api traces
//   - AI Guard inline policy enforcement
//
// LOG ANALYTICS WORKSPACE (Azure diagnostics):
// A Log Analytics workspace is provisioned here to capture AKS control-plane
// diagnostic logs (kube-apiserver, kube-controller-manager, kube-scheduler)
// and Azure resource-level metrics (Azure AI Search, Azure OpenAI).
// These complement the Datadog Azure Monitor integration.

@description('Azure region for the Log Analytics workspace')
param location string

@description('Environment tag value (e.g. dev, staging, prod)')
param environment string

var workspaceName = 'law-infra-advisor-${environment}'

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  tags: {
    environment: environment
    project: 'infra-advisor-ai'
  }
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

@description('Log Analytics workspace name')
output workspaceName string = logAnalyticsWorkspace.name

@description('Log Analytics workspace resource ID')
output workspaceId string = logAnalyticsWorkspace.id

@description('Log Analytics workspace Customer ID (used for AKS diagnostics binding)')
output workspaceCustomerId string = logAnalyticsWorkspace.properties.customerId
