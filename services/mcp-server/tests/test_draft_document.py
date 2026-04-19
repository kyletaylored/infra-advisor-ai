"""Tests for the draft_document MCP tool."""

import os
import pytest

os.environ.setdefault("DD_AGENT_HOST", "localhost")
os.environ.setdefault("DD_DOGSTATSD_PORT", "8125")

from src.tools.draft_document import DraftDocumentInput, draft_document


BRIDGE_CONTEXT = {
    "bridges": [
        {
            "STRUCTURE_NUMBER_008": "1100200000B0042",
            "FACILITY_CARRIED_007": "FM 1234",
            "LOCATION_009": "0.5 MI N OF COUNTY LINE",
            "SUFFICIENCY_RATING": 38.5,
            "DECK_COND_058": "4",
            "deck_condition_label": "poor",
            "SUPERSTRUCTURE_COND_059": "5",
            "superstructure_condition_label": "fair",
            "SUBSTRUCTURE_COND_060": "3",
            "substructure_condition_label": "serious",
            "STRUCTURALLY_DEFICIENT": "1",
            "ADT_029": 12500,
            "YEAR_BUILT_027": 1962,
            "INSPECT_DATE_090": "2021-06-15",
        }
    ],
    "asset_type": "bridge",
}

WATER_CONTEXT = {
    "water_systems": [
        {
            "PWSID": "TX0410076",
            "PWS_NAME": "Corpus Christi Water System",
            "CITY_NAME": "Corpus Christi",
            "POPULATION_SERVED_COUNT": 320000,
            "PRIMARY_SOURCE_CODE": "SW",
            "open_violation_count": 3,
            "_source": "EPA_SDWIS",
        }
    ],
    "water_plan_projects": [
        {
            "planning_region": "N",
            "project_name": "Corpus Christi Desalination Plant",
            "county": "Nueces",
            "strategy_type": "desalination",
            "_source": "TWDB_2026_State_Water_Plan",
        }
    ],
}


@pytest.mark.asyncio
async def test_scope_of_work_renders_with_bridge_data():
    """SOW template renders and includes bridge structure number."""
    result = await draft_document(
        DraftDocumentInput(
            document_type="scope_of_work",
            context=BRIDGE_CONTEXT,
            project_name="Bexar County Bridge Rehabilitation",
            client_name="Bexar County Transportation Department",
        )
    )
    assert isinstance(result, str), f"Expected str, got {type(result)}: {result}"
    assert "1100200000B0042" in result
    assert "Scope of Work" in result
    assert "Bexar County Bridge Rehabilitation" in result
    assert "Bexar County Transportation Department" in result


@pytest.mark.asyncio
async def test_scope_of_work_renders_without_optional_fields():
    """SOW template renders even when project_name and client_name are None."""
    result = await draft_document(
        DraftDocumentInput(
            document_type="scope_of_work",
            context={},
        )
    )
    assert isinstance(result, str)
    assert "Scope of Work" in result


@pytest.mark.asyncio
async def test_risk_summary_renders_with_bridge_data():
    """Risk summary template renders with bridge condition data."""
    result = await draft_document(
        DraftDocumentInput(
            document_type="risk_summary",
            context=BRIDGE_CONTEXT,
            project_name="Bridge Risk Assessment",
        )
    )
    assert isinstance(result, str)
    assert "Risk Summary" in result
    assert "1100200000B0042" in result
    assert "Risk Register" in result
    # Condition labels should appear
    assert "poor" in result or "serious" in result


@pytest.mark.asyncio
async def test_cost_estimate_scaffold_renders_with_bridge_data():
    """Cost estimate scaffold renders with bridge data and shows funding programs."""
    result = await draft_document(
        DraftDocumentInput(
            document_type="cost_estimate_scaffold",
            context=BRIDGE_CONTEXT,
            project_name="County Bridge Program",
        )
    )
    assert isinstance(result, str)
    assert "Cost Estimate" in result
    assert "1100200000B0042" in result
    assert "Phase 3" in result
    # Should mention bridge funding programs
    assert "IIJA" in result or "Highway Bridge" in result


@pytest.mark.asyncio
async def test_funding_positioning_memo_renders_with_water_data():
    """Funding memo renders with TWDB SWIFT eligibility checklist populated."""
    result = await draft_document(
        DraftDocumentInput(
            document_type="funding_positioning_memo",
            context=WATER_CONTEXT,
            project_name="Corpus Christi Desalination Feasibility",
            client_name="City of Corpus Christi",
        )
    )
    assert isinstance(result, str)
    assert "Funding Positioning Memo" in result
    # Must include TWDB SWIFT checklist
    assert "SWIFT" in result
    assert "TWDB" in result
    # Eligibility checklist items
    assert "Water Conservation Plan" in result
    assert "Asset Management Plan" in result
    # Water plan context referenced
    assert "2026 State Water Plan" in result or "$174B" in result


@pytest.mark.asyncio
async def test_funding_memo_swift_eligibility_checklist_present():
    """Funding memo must include TWDB SWIFT eligibility checklist items."""
    result = await draft_document(
        DraftDocumentInput(
            document_type="funding_positioning_memo",
            context=WATER_CONTEXT,
        )
    )
    assert isinstance(result, str)
    # All 8 SWIFT checklist items must be present
    checklist_items = [
        "recommended water management strategy",
        "political subdivision",
        "Water Conservation Plan",
        "Water Loss Audit",
        "Asset Management Plan",
        "Drought Contingency Plan",
        "Financial sustainability",
    ]
    for item in checklist_items:
        assert item in result, f"Missing SWIFT checklist item: {item!r}"


@pytest.mark.asyncio
async def test_unknown_document_type_returns_error():
    """Unknown document_type returns a structured error dict, not an exception."""
    # Pydantic will reject invalid literal at model validation time
    # Test the internal behavior by directly calling with a valid but unexpected type
    # (This tests the error branch in _TEMPLATE_MAP lookup)
    input_obj = DraftDocumentInput(
        document_type="scope_of_work",  # valid for model
        context={},
    )
    # Temporarily patch the template map to simulate unknown type
    from src.tools import draft_document as mod
    original_map = mod._TEMPLATE_MAP.copy()
    mod._TEMPLATE_MAP["scope_of_work"] = "nonexistent_template.md.j2"
    try:
        result = await draft_document(input_obj)
        assert isinstance(result, dict)
        assert "error" in result
        assert result.get("retriable") is False
    finally:
        mod._TEMPLATE_MAP.update(original_map)


@pytest.mark.asyncio
async def test_notes_injected_into_document():
    """Analyst notes are rendered in the output when provided."""
    result = await draft_document(
        DraftDocumentInput(
            document_type="scope_of_work",
            context=BRIDGE_CONTEXT,
            notes="Priority structure — ADT over 10,000 and last inspection before 2022.",
        )
    )
    assert isinstance(result, str)
    assert "Priority structure" in result
