// kafka.bicep — Kafka placeholder module for InfraAdvisor AI
//
// NOTE: Kafka is NOT provisioned as an Azure PaaS service.
// Per architecture decision (PRD § 2), Kafka runs via the Strimzi operator
// on AKS so that Datadog Data Streams Monitoring (DSM) can instrument the
// brokers via JMX annotations on the broker pods.
//
// Deployment approach:
//   1. Install the Strimzi operator into the `kafka` namespace via Helm:
//        helm repo add strimzi https://strimzi.io/charts/
//        helm upgrade --install strimzi-operator strimzi/strimzi-kafka-operator \
//          --namespace kafka --create-namespace
//   2. Apply the KafkaCluster custom resource from k8s/kafka/kafka-cluster.yaml.
//      Dev topology: single broker, single ZooKeeper (not production HA).
//   3. Topics provisioned via KafkaTopic CRs in k8s/kafka/:
//        infra.query.events   — load-generator → agent-api
//        infra.eval.results   — eval scores → Datadog custom metrics pipeline
//   4. Datadog Kafka + JMX integration is enabled via pod annotations on the
//      broker StatefulSet (see k8s/datadog/ for the Datadog agent values).
//
// This module outputs the in-cluster bootstrap address so that main.bicep
// can surface it as a deployment output for reference.

@description('Placeholder: Kafka runs on AKS via Strimzi, not as Azure PaaS')
output kafkaBootstrapServers string = 'kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092'
