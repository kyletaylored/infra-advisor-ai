"""Background Kafka consumer for the agent-api service.

Consumes from ``infra.query.events``, runs each query through the InfraAdvisor
agent, and produces results to ``infra.eval.results``.

DSM instrumentation is automatic via ``import ddtrace.auto`` in main.py (which
runs before this module is imported).

Usage
-----
Call ``start_consumer_thread(mcp_client, llm)`` from the FastAPI lifespan; the
thread runs as a daemon and stops when the process exits.
"""

import asyncio
import json
import logging
import os
import threading
import time

from confluent_kafka import Consumer, KafkaError, Producer

logger = logging.getLogger(__name__)

KAFKA_BOOTSTRAP = os.environ.get(
    "KAFKA_BOOTSTRAP_SERVERS", "kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092"
)
TOPIC_QUERIES = "infra.query.events"
TOPIC_RESULTS = "infra.eval.results"
GROUP_ID = "infra-advisor-agent-api"


def _build_consumer() -> Consumer:
    return Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "group.id": GROUP_ID,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
        }
    )


def _build_producer() -> Producer:
    return Producer({"bootstrap.servers": KAFKA_BOOTSTRAP})


def _run_consumer_loop(mcp_client) -> None:
    """Blocking consumer loop — runs in a background thread."""
    from agent import run_agent
    from memory import get_session_model

    consumer = _build_consumer()
    producer = _build_producer()
    consumer.subscribe([TOPIC_QUERIES])

    # Each iteration of the loop needs its own event loop since run_agent is async
    loop = asyncio.new_event_loop()

    logger.info("Kafka consumer thread started — subscribed to %s", TOPIC_QUERIES)

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                logger.error("Kafka consumer error: %s", msg.error())
                continue

            try:
                event = json.loads(msg.value().decode())
            except Exception as exc:
                logger.warning("Failed to decode Kafka message: %s", exc)
                continue

            query = event.get("query", "")
            session_id = event.get("session_id", "unknown")
            corpus_type = event.get("corpus_type", "unknown")
            domain = event.get("domain", "general")
            query_id = event.get("query_id", "unknown")

            logger.info(
                "Processing Kafka query id=%s session=%s corpus=%s",
                query_id,
                session_id,
                corpus_type,
            )

            start_ms = time.monotonic() * 1000
            try:
                deployment = loop.run_until_complete(get_session_model(session_id))
                result = loop.run_until_complete(
                    run_agent(
                        query=query,
                        session_id=session_id,
                        mcp_client=mcp_client,
                        deployment=deployment,
                    )
                )
                latency_ms = time.monotonic() * 1000 - start_ms

                eval_result = {
                    "session_id": session_id,
                    "query_id": query_id,
                    "query": query,
                    "answer": result.get("answer", ""),
                    "sources": result.get("sources", []),
                    "tools_called": result.get("tools_called", []),
                    "faithfulness_score": None,  # computed async by llm_obs
                    "latency_ms": latency_ms,
                    "corpus_type": corpus_type,
                    "domain": domain,
                }

                producer.produce(
                    TOPIC_RESULTS,
                    key=session_id.encode(),
                    value=json.dumps(eval_result).encode(),
                )
                producer.poll(0)
                logger.info(
                    "Produced eval result for query_id=%s latency_ms=%.0f",
                    query_id,
                    latency_ms,
                )

            except Exception as exc:
                logger.error("Agent error for query_id=%s: %s", query_id, exc)

    except Exception as exc:
        logger.error("Kafka consumer loop crashed: %s", exc)
    finally:
        consumer.close()
        producer.flush(timeout=5)
        loop.close()
        logger.info("Kafka consumer thread stopped")


def start_consumer_thread(mcp_client) -> threading.Thread:
    """Start the Kafka consumer in a background daemon thread.

    Returns the thread object (already started).
    """
    thread = threading.Thread(
        target=_run_consumer_loop,
        args=(mcp_client,),
        daemon=True,
        name="kafka-consumer",
    )
    thread.start()
    logger.info("Kafka consumer daemon thread started")
    return thread
