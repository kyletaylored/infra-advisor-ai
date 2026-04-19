import ddtrace.auto  # must be first import — enables DSM and APM instrumentation

import hashlib
import json
import logging
import os
import random
import time
import uuid
from pathlib import Path

import httpx
import yaml
from confluent_kafka import Producer
from ddtrace import tracer

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Configuration ─────────────────────────────────────────────────────────────

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka-broker.kafka.svc.cluster.local:9092")
KAFKA_TOPIC_QUERIES = "infra.query.events"
AGENT_API_URL = os.environ.get("AGENT_API_URL", "http://agent-api.infra-advisor.svc.cluster.local:8001")

CORPUS_DIR = Path(__file__).parent / "corpus"

# Distribution: 70% happy, 20% edge, 10% adversarial
_CORPUS_WEIGHTS = {
    "happy_path": 0.70,
    "edge_cases": 0.20,
    "adversarial": 0.10,
}

QUERIES_PER_RUN_MIN = 10
QUERIES_PER_RUN_MAX = 20


# ─── Kafka producer ────────────────────────────────────────────────────────────

def _build_producer() -> Producer:
    return Producer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "client.id": "infra-advisor-load-generator",
            # DSM — ddtrace instruments confluent-kafka automatically when ddtrace.auto imported
        }
    )


def _delivery_callback(err, msg):
    if err:
        logger.error("Kafka delivery failed: %s", err)
    else:
        logger.debug(
            "Delivered to %s [%d] offset %d",
            msg.topic(),
            msg.partition(),
            msg.offset(),
        )


# ─── Corpus loader ─────────────────────────────────────────────────────────────

def _load_corpus(name: str) -> list[dict]:
    path = CORPUS_DIR / f"{name}.yaml"
    if not path.exists():
        logger.warning("Corpus file not found: %s", path)
        return []
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("queries", [])


def _sample_queries(count: int) -> list[tuple[str, dict]]:
    """Sample `count` queries with the distribution defined in _CORPUS_WEIGHTS."""
    corpora = {name: _load_corpus(name) for name in _CORPUS_WEIGHTS}
    samples: list[tuple[str, dict]] = []

    population = []
    weights = []
    for corpus_name, weight in _CORPUS_WEIGHTS.items():
        entries = corpora.get(corpus_name, [])
        for entry in entries:
            population.append((corpus_name, entry))
            weights.append(weight / max(len(entries), 1))

    if not population:
        logger.error("All corpora are empty — no queries to sample")
        return []

    # Normalise weights
    total = sum(weights)
    weights = [w / total for w in weights]

    chosen = random.choices(population, weights=weights, k=count)
    return chosen


# ─── Expected answer hash ──────────────────────────────────────────────────────

def _answer_hash(query: str) -> str:
    """Stable hash of query text for deduplication / evaluation matching."""
    return hashlib.sha256(query.encode()).hexdigest()[:16]


# ─── Main execution ────────────────────────────────────────────────────────────

def run() -> None:
    query_count = random.randint(QUERIES_PER_RUN_MIN, QUERIES_PER_RUN_MAX)
    logger.info("Load generator run starting — firing %d queries", query_count)

    producer = _build_producer()
    samples = _sample_queries(query_count)

    with tracer.trace("load_generator.run", service="infra-advisor-load-generator") as span:
        span.set_tag("query_count", query_count)

        for corpus_type, entry in samples:
            session_id = str(uuid.uuid4())
            query_text = entry.get("query", "")
            query_id = entry.get("id", "unknown")
            domain = entry.get("domain", "general")

            event = {
                "query_id": query_id,
                "session_id": session_id,
                "query": query_text,
                "corpus_type": corpus_type,
                "domain": domain,
                "expected_answer_hash": _answer_hash(query_text),
                "timestamp_ms": int(time.time() * 1000),
            }

            # Produce to Kafka (DSM-instrumented by ddtrace)
            producer.produce(
                KAFKA_TOPIC_QUERIES,
                key=session_id.encode(),
                value=json.dumps(event).encode(),
                callback=_delivery_callback,
            )
            producer.poll(0)

            logger.info(
                "Queued query id=%s corpus=%s domain=%s session=%s",
                query_id,
                corpus_type,
                domain,
                session_id,
            )

        producer.flush(timeout=10)
        logger.info("Load generator run complete — %d queries produced", len(samples))
        span.set_tag("queries_produced", len(samples))


if __name__ == "__main__":
    run()
