import ddtrace.auto  # must be first import — enables APM auto-instrumentation

"""
fetch_public_docs.py
====================
Fetches real infrastructure documents from public data sources and upserts
them into Azure AI Search to supplement the synthetic knowledge base.

Sources:
  1. OpenFEMA Disaster Summaries   — disaster profiles by US state
  2. OpenFEMA Hazard Mitigation    — HM grant project categories by state
  3. EIA State Energy Profiles     — electricity generation/capacity by state (requires EIA_API_KEY)
  4. NBI County Bridge Summaries   — county-level bridge condition summaries via NBI bridge API
"""

import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import httpx
import tiktoken
from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from openai import AzureOpenAI

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CHUNK_SIZE_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 64
EMBEDDING_MODEL = "text-embedding-3-small"
EXISTING_THRESHOLD = 200  # skip run if >= this many real (non-synthetic) docs exist

STATE_FIPS = {
    "TX": "48",
    "CA": "06",
    "FL": "12",
    "LA": "22",
    "OK": "40",
    "AZ": "04",
}

STATE_NAMES = {
    "TX": "Texas",
    "CA": "California",
    "FL": "Florida",
    "LA": "Louisiana",
    "OK": "Oklahoma",
    "AZ": "Arizona",
}

TARGET_STATES = ["TX", "LA", "FL", "OK", "AZ", "CA"]

FEMA_DISASTER_URL = (
    "https://www.fema.gov/api/open/v2/DisasterDeclarationsSummaries"
)
FEMA_HM_URL = (
    "https://www.fema.gov/api/open/v2/HazardMitigationGrantProgramProjectActivities"
)
EIA_RETAIL_URL = "https://api.eia.gov/v2/electricity/retail-sales/data"
NBI_BRIDGE_URL = "https://bridgeapi.azurewebsites.net/api/bridges"

CONDITION_LABELS = {
    "9": "excellent", "8": "very good", "7": "good",
    "6": "satisfactory", "5": "fair", "4": "poor",
    "3": "serious", "2": "critical", "1": "imminent failure", "0": "failed",
}


# ---------------------------------------------------------------------------
# Fetcher functions
# ---------------------------------------------------------------------------

def fetch_fema_disaster_profiles(states: list[str]) -> list[dict]:
    """Fetch OpenFEMA DisasterDeclarationsSummaries and build one doc per state."""
    docs = []
    year_now = datetime.now(timezone.utc).year

    for state in states:
        state_name = STATE_NAMES.get(state, state)
        url = FEMA_DISASTER_URL
        params = {
            "$format": "json",
            "$top": 1000,
            "$filter": f"state eq '{state}'",
        }

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.warning("FEMA disaster fetch failed for %s: %s", state, exc)
            continue

        records = data.get("DisasterDeclarationsSummaries", [])
        if not records:
            log.info("No FEMA disaster records returned for %s", state)
            continue

        log.info("Fetched %d FEMA disaster records for %s", len(records), state)

        # Aggregate by incident type
        by_type: dict[str, dict] = defaultdict(lambda: {"count": 0, "counties": set()})
        all_years = []
        recent_disasters = []

        for rec in records:
            itype = rec.get("incidentType", "Unknown")
            by_type[itype]["count"] += 1
            county = rec.get("designatedArea", "")
            if county:
                by_type[itype]["counties"].add(county)

            decl_date = rec.get("declarationDate", "")
            if decl_date:
                try:
                    yr = int(decl_date[:4])
                    all_years.append(yr)
                    if yr >= year_now - 5:
                        recent_disasters.append({
                            "title": rec.get("declarationTitle", ""),
                            "date": decl_date[:10],
                            "counties": rec.get("designatedArea", ""),
                        })
                except (ValueError, IndexError):
                    pass

        earliest = min(all_years) if all_years else "unknown"
        latest = max(all_years) if all_years else "unknown"
        total = len(records)

        # Build breakdown text
        breakdown_lines = []
        for itype, info in sorted(by_type.items(), key=lambda x: -x[1]["count"]):
            breakdown_lines.append(
                f"- {itype}: {info['count']} declarations, "
                f"{len(info['counties'])} counties affected"
            )

        # Build recent disasters text (up to 5)
        recent_lines = []
        for d in sorted(recent_disasters, key=lambda x: x["date"], reverse=True)[:5]:
            recent_lines.append(
                f"- {d['title']}: declared {d['date']}, counties: {d['counties']}"
            )

        # Infrastructure implications paragraph
        top_types = sorted(by_type.items(), key=lambda x: -x[1]["count"])[:3]
        top_names = [t[0] for t in top_types]
        implications = (
            f"{state_name} faces recurring infrastructure risk from "
            f"{', '.join(top_names)}. These events drive demand for hardened "
            f"transportation and utility infrastructure, FEMA Public Assistance-"
            f"eligible repairs, and Hazard Mitigation Grant Program investments "
            f"targeting flood control, bridge scour protection, and grid resilience. "
            f"Frequency trends suggest increasing exposure to climate-driven events "
            f"requiring proactive capital planning."
        )

        content = (
            f"# FEMA Disaster Profile — {state_name}\n\n"
            f"## Summary\n"
            f"Total disaster declarations: {total}\n"
            f"States covered: {state}\n"
            f"Period: {earliest} – {latest}\n\n"
            f"## Disaster Type Breakdown\n"
            + "\n".join(breakdown_lines)
            + "\n\n## Notable Recent Disasters (last 5 years)\n"
            + ("\n".join(recent_lines) if recent_lines else "- No recent declarations in dataset")
            + f"\n\n## Infrastructure Implications\n{implications}\n"
        )

        docs.append({
            "id": f"fema_disaster_{state.lower()}_{year_now}",
            "content": content,
            "source": "OpenFEMA_Disaster_Declarations",
            "document_type": "disaster_profile",
            "domain": "disaster",
            "source_url": FEMA_DISASTER_URL,
        })

    return docs


def fetch_fema_hm_projects(states: list[str]) -> list[dict]:
    """Fetch OpenFEMA Hazard Mitigation project activities and build one doc per state."""
    docs = []
    year_now = datetime.now(timezone.utc).year

    for state in states:
        state_name = STATE_NAMES.get(state, state)
        params = {
            "$format": "json",
            "$top": 200,
            "$filter": f"state eq '{state}'",
        }

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(FEMA_HM_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.warning("FEMA HM fetch failed for %s: %s", state, exc)
            continue

        records = data.get("HazardMitigationGrantProgramProjectActivities", [])
        if not records:
            log.info("No FEMA HM records returned for %s", state)
            continue

        log.info("Fetched %d FEMA HM records for %s", len(records), state)

        # Group by project type
        by_type: dict[str, dict] = defaultdict(lambda: {"count": 0, "cost": 0.0})
        for rec in records:
            ptype = rec.get("projectType", "Unknown") or "Unknown"
            by_type[ptype]["count"] += 1
            cost_str = rec.get("federalShareObligated", "") or "0"
            try:
                by_type[ptype]["cost"] += float(cost_str)
            except (ValueError, TypeError):
                pass

        total_projects = len(records)
        total_cost = sum(v["cost"] for v in by_type.values())

        breakdown_lines = []
        for ptype, info in sorted(by_type.items(), key=lambda x: -x[1]["count"]):
            cost_m = info["cost"] / 1_000_000
            breakdown_lines.append(
                f"- {ptype}: {info['count']} projects, "
                f"${cost_m:.1f}M federal share obligated"
            )

        content = (
            f"# FEMA Hazard Mitigation Grant Program — {state_name}\n\n"
            f"## Summary\n"
            f"Total HM project activities: {total_projects}\n"
            f"Total federal share obligated: ${total_cost / 1_000_000:.1f}M\n"
            f"State: {state}\n\n"
            f"## Project Type Breakdown\n"
            + "\n".join(breakdown_lines)
            + "\n\n## Strategic Context\n"
            f"Hazard Mitigation Grant Program investments in {state_name} reflect "
            f"the state's disaster risk profile. The project mix indicates priority "
            f"areas for resilience investment including flood mitigation, structural "
            f"hardening, and utility protection. These grant categories align with "
            f"FEMA BRIC competitive program priorities and inform benefit-cost analysis "
            f"strategies for future grant applications.\n"
        )

        docs.append({
            "id": f"fema_hm_{state.lower()}_{year_now}",
            "content": content,
            "source": "OpenFEMA_Hazard_Mitigation",
            "document_type": "hazard_mitigation_report",
            "domain": "disaster",
            "source_url": FEMA_HM_URL,
        })

    return docs


def fetch_eia_state_profiles(states: list[str]) -> list[dict]:
    """Fetch EIA retail electricity sales data and build one energy profile doc per state."""
    eia_key = os.environ.get("EIA_API_KEY")
    if not eia_key:
        log.warning("EIA_API_KEY not set — skipping EIA state energy profiles.")
        return []

    docs = []
    year_now = datetime.now(timezone.utc).year

    for state in states:
        state_name = STATE_NAMES.get(state, state)
        params = {
            "api_key": eia_key,
            "frequency": "annual",
            "facets[stateid][]": state,
            "data[0]": "price",
            "data[1]": "sales",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 10,
        }

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(EIA_RETAIL_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.warning("EIA fetch failed for %s: %s", state, exc)
            continue

        rows = data.get("response", {}).get("data", [])
        if not rows:
            log.info("No EIA data returned for %s", state)
            continue

        log.info("Fetched %d EIA rows for %s", len(rows), state)

        # Group by period (year) and sector
        by_year: dict[str, list] = defaultdict(list)
        for row in rows:
            period = row.get("period", "")
            by_year[period].append(row)

        year_lines = []
        for period in sorted(by_year.keys(), reverse=True):
            period_rows = by_year[period]
            prices = [r.get("price") for r in period_rows if r.get("price") is not None]
            sales = [r.get("sales") for r in period_rows if r.get("sales") is not None]
            avg_price = sum(float(p) for p in prices) / len(prices) if prices else None
            total_sales = sum(float(s) for s in sales) if sales else None
            price_str = f"{avg_price:.2f} cents/kWh" if avg_price is not None else "N/A"
            sales_str = f"{total_sales:,.0f} MWh" if total_sales is not None else "N/A"
            year_lines.append(
                f"- {period}: average retail price {price_str}, total sales {sales_str}"
            )

        content = (
            f"# EIA State Energy Profile — {state_name}\n\n"
            f"## Summary\n"
            f"State: {state}\n"
            f"Source: U.S. Energy Information Administration (EIA) Retail Electricity Sales\n"
            f"Data coverage: last 10 annual periods\n\n"
            f"## Annual Retail Electricity Sales and Prices\n"
            + "\n".join(year_lines)
            + "\n\n## Infrastructure Context\n"
            f"Retail electricity price and consumption trends in {state_name} reflect "
            f"the state's generation mix, transmission infrastructure condition, and "
            f"demand growth driven by population and industrial activity. Price variability "
            f"signals grid stress periods and the value of demand-side management, "
            f"distributed generation, and resilience investments. These data inform "
            f"energy infrastructure planning, IIJA grid resilience grant applications, "
            f"and utility rate analysis for municipal clients.\n"
        )

        docs.append({
            "id": f"eia_state_{state.lower()}_{year_now}",
            "content": content,
            "source": "EIA_Electricity_Retail",
            "document_type": "state_energy_profile",
            "domain": "energy",
            "source_url": EIA_RETAIL_URL,
        })

    return docs


def fetch_nbi_county_summaries(states: list[str]) -> list[dict]:
    """Fetch NBI bridge data per state and produce county-level condition summary docs."""
    docs = []
    year_now = datetime.now(timezone.utc).year

    for state in states:
        fips = STATE_FIPS.get(state)
        state_name = STATE_NAMES.get(state, state)
        if not fips:
            log.warning("No FIPS code for state %s — skipping NBI fetch", state)
            continue

        params = {"state": fips, "limit": 500}

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(NBI_BRIDGE_URL, params=params)
                resp.raise_for_status()
                bridges = resp.json()
        except Exception as exc:
            log.warning("NBI fetch failed for %s (FIPS %s): %s", state, fips, exc)
            continue

        if not bridges or not isinstance(bridges, list):
            log.info("No NBI bridge records returned for %s", state)
            continue

        log.info("Fetched %d NBI bridge records for %s", len(bridges), state)

        total_bridges = len(bridges)

        # Group by county
        by_county: dict[str, list] = defaultdict(list)
        for bridge in bridges:
            county = str(bridge.get("COUNTY_CODE_003", "Unknown"))
            by_county[county].append(bridge)

        # Overall stats
        sd_count = sum(
            1 for b in bridges
            if str(b.get("STRUCTURALLY_DEFICIENT", "")) == "1"
        )
        pre_1970 = sum(
            1 for b in bridges
            if b.get("YEAR_BUILT_027") and int(b.get("YEAR_BUILT_027", 9999)) < 1970
        )
        scour_critical = sum(
            1 for b in bridges
            if str(b.get("SCOUR_CRITICAL_113", "")) in ("U", "3", "2")
        )
        sd_pct = (sd_count / total_bridges * 100) if total_bridges else 0

        # Top 5 worst counties by deficiency rate
        county_stats = []
        for county, cbridges in by_county.items():
            c_total = len(cbridges)
            c_sd = sum(
                1 for b in cbridges
                if str(b.get("STRUCTURALLY_DEFICIENT", "")) == "1"
            )
            c_rate = c_sd / c_total if c_total else 0
            county_stats.append((county, c_total, c_sd, c_rate))

        county_stats.sort(key=lambda x: -x[3])
        top5_lines = []
        for county, c_total, c_sd, c_rate in county_stats[:5]:
            top5_lines.append(
                f"- County {county}: {c_sd}/{c_total} structurally deficient "
                f"({c_rate * 100:.1f}% deficiency rate)"
            )

        content = (
            f"# NBI Bridge Inventory Summary — {state_name}\n\n"
            f"## Overall Statistics\n"
            f"Total bridges surveyed: {total_bridges}\n"
            f"Structurally deficient: {sd_count} ({sd_pct:.1f}%)\n"
            f"Built before 1970: {pre_1970} ({pre_1970 / total_bridges * 100:.1f}% of inventory)\n"
            f"Scour-critical: {scour_critical}\n"
            f"Counties represented: {len(by_county)}\n\n"
            f"## Top 5 Counties by Structural Deficiency Rate\n"
            + "\n".join(top5_lines if top5_lines else ["- No county data available"])
            + "\n\n## Infrastructure Implications\n"
            f"The bridge inventory in {state_name} shows {sd_pct:.1f}% structural deficiency, "
            f"with {pre_1970} structures built before modern AASHTO LRFD design standards. "
            f"Scour-critical designations at {scour_critical} bridges indicate flood vulnerability "
            f"requiring HEC-18 assessments and countermeasure investment. FHWA Bridge Formula "
            f"Program funds under IIJA provide a priority funding pathway for rehabilitation "
            f"of structurally deficient bridges, particularly in counties with deficiency rates "
            f"exceeding the national average of approximately 7%.\n"
        )

        docs.append({
            "id": f"nbi_county_summary_{state.lower()}_{year_now}",
            "content": content,
            "source": "FHWA_NBI",
            "document_type": "bridge_inventory_summary",
            "domain": "transportation",
            "source_url": NBI_BRIDGE_URL,
        })

    return docs


# ---------------------------------------------------------------------------
# Chunking / embedding / indexing helpers
# ---------------------------------------------------------------------------

def _chunk_text(text: str, enc: tiktoken.Encoding) -> list[str]:
    tokens = enc.encode(text)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + CHUNK_SIZE_TOKENS, len(tokens))
        chunks.append(enc.decode(tokens[start:end]))
        if end == len(tokens):
            break
        start += CHUNK_SIZE_TOKENS - CHUNK_OVERLAP_TOKENS
    return chunks


def _embed(client: AzureOpenAI, text: str) -> list[float]:
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return response.data[0].embedding


def _upsert_doc(
    search_client: SearchClient,
    oai_client: AzureOpenAI,
    doc: dict,
    enc: tiktoken.Encoding,
) -> int:
    """Chunk, embed, and upsert a single document into Azure AI Search."""
    chunks = _chunk_text(doc["content"], enc)
    now_iso = datetime.now(timezone.utc).isoformat()
    batch = []

    for chunk_idx, chunk_text in enumerate(chunks):
        chunk_id = f"{doc['id']}_{chunk_idx}"
        vector = _embed(oai_client, chunk_text)
        batch.append({
            "id": chunk_id,
            "content": chunk_text,
            "content_vector": vector,
            "source": doc["source"],
            "document_type": doc["document_type"],
            "domain": doc["domain"],
            "last_updated": now_iso,
            "chunk_index": chunk_idx,
            "source_url": doc.get("source_url"),
        })

    if batch:
        search_client.upsert_documents(documents=batch)

    return len(chunks)


def _count_existing_real_docs(search_client: SearchClient) -> int:
    """Return count of non-synthetic documents already in the index."""
    results = search_client.search(
        search_text="*",
        filter="source ne 'synthetic'",
        select=["id"],
        include_total_count=True,
        top=0,
    )
    count = results.get_count()
    return count if count is not None else 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    """
    Fetch real public infrastructure documents and index them into Azure AI Search.

    Idempotent: if the index already has >= EXISTING_THRESHOLD (200) non-synthetic
    documents, the script exits without making API calls.
    """
    log.info("fetch_public_docs starting — reading environment configuration...")

    # Fail fast on missing Azure credentials
    openai_endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    openai_api_key = os.environ["AZURE_OPENAI_API_KEY"]
    embedding_deployment = os.environ.get(
        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-small"
    )
    search_endpoint = os.environ["AZURE_SEARCH_ENDPOINT"]
    search_api_key = os.environ["AZURE_SEARCH_API_KEY"]
    index_name = os.environ.get("AZURE_SEARCH_INDEX_NAME", "infra-advisor-knowledge")

    oai_client = AzureOpenAI(
        azure_endpoint=openai_endpoint,
        api_key=openai_api_key,
        api_version="2024-02-01",
    )

    search_client = SearchClient(
        endpoint=search_endpoint,
        index_name=index_name,
        credential=AzureKeyCredential(search_api_key),
    )

    enc = tiktoken.get_encoding("cl100k_base")

    # Idempotency check
    existing_count = _count_existing_real_docs(search_client)
    log.info("Found %d existing non-synthetic documents in index.", existing_count)
    if existing_count >= EXISTING_THRESHOLD:
        log.info(
            "Index already has %d real documents (>= threshold %d). Exiting.",
            existing_count,
            EXISTING_THRESHOLD,
        )
        return

    # Run all four fetchers
    log.info("Fetching documents for states: %s", TARGET_STATES)

    raw_docs: list[dict] = []

    log.info("Running fetch_fema_disaster_profiles...")
    try:
        raw_docs.extend(fetch_fema_disaster_profiles(TARGET_STATES))
    except Exception as exc:
        log.error("fetch_fema_disaster_profiles failed: %s", exc, exc_info=True)

    log.info("Running fetch_fema_hm_projects...")
    try:
        raw_docs.extend(fetch_fema_hm_projects(TARGET_STATES))
    except Exception as exc:
        log.error("fetch_fema_hm_projects failed: %s", exc, exc_info=True)

    log.info("Running fetch_eia_state_profiles...")
    try:
        raw_docs.extend(fetch_eia_state_profiles(TARGET_STATES))
    except Exception as exc:
        log.error("fetch_eia_state_profiles failed: %s", exc, exc_info=True)

    log.info("Running fetch_nbi_county_summaries...")
    try:
        raw_docs.extend(fetch_nbi_county_summaries(TARGET_STATES))
    except Exception as exc:
        log.error("fetch_nbi_county_summaries failed: %s", exc, exc_info=True)

    log.info("Total documents to index: %d", len(raw_docs))

    total_chunks = 0
    indexed_count = 0

    for doc in raw_docs:
        doc_id = doc["id"]
        log.info("Indexing document: %s (source=%s)", doc_id, doc["source"])
        try:
            chunk_count = _upsert_doc(search_client, oai_client, doc, enc)
            log.info("  Indexed %d chunks for %s", chunk_count, doc_id)
            total_chunks += chunk_count
            indexed_count += 1
        except Exception as exc:
            log.error("  ERROR indexing %s: %s", doc_id, exc, exc_info=True)
            continue

    log.info(
        "fetch_public_docs complete. Indexed: %d/%d documents, %d total chunks.",
        indexed_count,
        len(raw_docs),
        total_chunks,
    )


if __name__ == "__main__":
    main()
