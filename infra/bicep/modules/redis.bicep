// redis.bicep — Redis placeholder module for InfraAdvisor AI
//
// NOTE: Redis is NOT provisioned as Azure Cache for Redis.
// Per architecture decision (PRD § 2), Redis runs as a plain Kubernetes
// Deployment in the `infra-advisor` namespace on AKS.
//
// Rationale: The lab uses a single-replica Redis pod (no persistence, no
// HA) sized for session-memory caching and LangChain ConversationBufferMemory.
// Azure Cache for Redis would add ~$55/month (C1 Basic) with no additional
// benefit for this demo workload.
//
// Deployment approach:
//   1. Apply the Redis Deployment and Service from k8s/redis/:
//        kubectl apply -f k8s/redis/
//   2. Redis listens on port 6379 inside the cluster.
//   3. Datadog Redis integration and DBM are enabled via pod annotations on
//      the Redis Deployment (see k8s/datadog/ for the Datadog agent values).
//
// This module outputs the in-cluster service address so that main.bicep
// can surface it as a deployment output for reference.

@description('Placeholder: Redis runs as a K8s Deployment in the infra-advisor namespace, not as Azure Cache for Redis')
output redisConnectionString string = 'redis.infra-advisor.svc.cluster.local:6379'
