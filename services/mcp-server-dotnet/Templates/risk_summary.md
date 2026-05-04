# Risk Summary
## {{ project_name | default.if_empty "Infrastructure Risk Assessment" }}

**Prepared for:** {{ client_name | default.if_empty "[Client Name]" }}
**Date:** {{ generated_date | default.if_empty "[Date]" }}
**Assessment Type:** Infrastructure Risk Register

---

## Executive Summary

{{ if context.bridges && context.bridges.size > 0 }}
This risk summary covers **{{ context.bridges.size }}** bridge structure(s) assessed from FHWA National Bridge Inventory data. Structures are ranked by risk priority combining sufficiency rating, condition codes, traffic exposure, and scour vulnerability.
{{ else if context.water_systems && context.water_systems.size > 0 }}
This risk summary covers **{{ context.water_systems.size }}** public water system(s) with open Safe Drinking Water Act violations or infrastructure deficiencies.
{{ else if context.disaster_declarations && context.disaster_declarations.size > 0 }}
This risk summary covers infrastructure risk based on **{{ context.disaster_declarations.size }}** FEMA disaster declaration(s) affecting the study area.
{{ else }}
[Insert executive summary of key risks identified for this project.]
{{ end }}

{{ if notes }}
**Analyst Notes:** {{ notes }}
{{ end }}

---

## Risk Register

### Likelihood Scale
| Score | Label | Description |
|---|---|---|
| 5 | Almost Certain | Expected to occur within 1 year |
| 4 | Likely | Expected to occur within 2–5 years |
| 3 | Possible | May occur within 5–10 years |
| 2 | Unlikely | Not expected but possible within 10 years |
| 1 | Rare | May occur only in exceptional circumstances |

### Consequence Scale
| Score | Label | Description |
|---|---|---|
| 5 | Catastrophic | Fatalities, loss of critical service, >$10M cost |
| 4 | Major | Serious injury, extended service disruption, $1M–$10M |
| 3 | Moderate | Medical treatment, short service disruption, $100K–$1M |
| 2 | Minor | First aid, minor disruption, $10K–$100K |
| 1 | Negligible | Minimal impact, <$10K cost |

### Risk Matrix
*Risk Score = Likelihood × Consequence*

|  | **5 Catastrophic** | **4 Major** | **3 Moderate** | **2 Minor** | **1 Negligible** |
|---|---|---|---|---|---|
| **5 Almost Certain** | 25 🔴 | 20 🔴 | 15 🟠 | 10 🟡 | 5 🟢 |
| **4 Likely** | 20 🔴 | 16 🔴 | 12 🟠 | 8 🟡 | 4 🟢 |
| **3 Possible** | 15 🟠 | 12 🟠 | 9 🟡 | 6 🟡 | 3 🟢 |
| **2 Unlikely** | 10 🟡 | 8 🟡 | 6 🟡 | 4 🟢 | 2 🟢 |
| **1 Rare** | 5 🟢 | 4 🟢 | 3 🟢 | 2 🟢 | 1 🟢 |

*🔴 High (≥15) | 🟠 Medium-High (9–14) | 🟡 Medium (4–8) | 🟢 Low (1–3)*

---

## Identified Risks

{{ if context.bridges && context.bridges.size > 0 }}
{{ for bridge in context.bridges }}
### Risk {{ for.index + 1 }}: Structure {{ bridge.STRUCTURE_NUMBER_008 | default.if_empty "[#]" }}

| Field | Value |
|---|---|
| Structure Number | {{ bridge.STRUCTURE_NUMBER_008 | default.if_empty "N/A" }} |
| Location | {{ bridge.FACILITY_CARRIED_007 | default.if_empty "N/A" }} — {{ bridge.LOCATION_009 | default.if_empty "N/A" }} |
| Deck Condition | {{ bridge.deck_condition | default.if_empty bridge.DECK_COND_058 | default.if_empty "N/A" }} |
| Superstructure | {{ bridge.superstructure_condition | default.if_empty bridge.SUPERSTRUCTURE_COND_059 | default.if_empty "N/A" }} |
| Substructure | {{ bridge.substructure_condition | default.if_empty bridge.SUBSTRUCTURE_COND_060 | default.if_empty "N/A" }} |
| Lowest Rating | {{ bridge.lowest_rating | default.if_empty "N/A" }} / 9 |
| ADT | {{ bridge.adt | default.if_empty "N/A" }} vehicles/day |
| Year Built | {{ bridge.year_built | default.if_empty "N/A" }} |
| Last Inspection | {{ bridge.last_inspection_date | default.if_empty "N/A" }} |
| Structurally Deficient | {{ if bridge.structurally_deficient }}YES ⚠️{{ else }}No{{ end }} |

| Risk Category | Likelihood | Consequence | Risk Score | Priority |
|---|---|---|---|---|
| Structural failure under loading | [L] | [C] | [L×C] | [H/M/L] |
| Scour/undermining at flood | [L] | [C] | [L×C] | [H/M/L] |
| Deck deterioration/spalling | [L] | [C] | [L×C] | [H/M/L] |
| Loss of load-carrying capacity | [L] | [C] | [L×C] | [H/M/L] |

**Recommended Actions:**
1. [Action 1 — immediate term]
2. [Action 2 — short term]
3. [Action 3 — long term]

---
{{ end }}
{{ else if context.water_systems && context.water_systems.size > 0 }}
{{ for system in context.water_systems }}
### Risk {{ for.index + 1 }}: {{ system.system_name | default.if_empty system.PWS_NAME | default.if_empty "[System Name]" }}

| Field | Value |
|---|---|
| PWSID | {{ system.pwsid | default.if_empty system.PWSID | default.if_empty "N/A" }} |
| City | {{ system.city | default.if_empty system.CITY_NAME | default.if_empty "N/A" }} |
| Population Served | {{ system.population_served | default.if_empty system.POPULATION_SERVED_COUNT | default.if_empty "N/A" }} |
| Primary Source | {{ system.primary_source_type | default.if_empty system.PRIMARY_SOURCE_CODE | default.if_empty "N/A" }} |
| Open Violations | {{ system.open_violation_count | default.if_empty "N/A" }} |
| _Source | {{ system._source | default.if_empty "EPA_SDWIS" }} |

| Risk Category | Likelihood | Consequence | Risk Score | Priority |
|---|---|---|---|---|
| SDWA violation enforcement action | [L] | [C] | [L×C] | [H/M/L] |
| Treatment failure | [L] | [C] | [L×C] | [H/M/L] |
| Source water contamination | [L] | [C] | [L×C] | [H/M/L] |
| Infrastructure failure | [L] | [C] | [L×C] | [H/M/L] |

---
{{ end }}
{{ else }}
| Risk ID | Risk Description | Category | Likelihood (1–5) | Consequence (1–5) | Risk Score | Priority | Mitigation Strategy | Owner |
|---|---|---|---|---|---|---|---|---|
| R-001 | [Risk description] | [Category] | [1–5] | [1–5] | [Score] | [H/M/L] | [Strategy] | [Owner] |
| R-002 | [Risk description] | [Category] | [1–5] | [1–5] | [Score] | [H/M/L] | [Strategy] | [Owner] |
| R-003 | [Risk description] | [Category] | [1–5] | [1–5] | [Score] | [H/M/L] | [Strategy] | [Owner] |
{{ end }}

---

## Risk Response Plan

| Priority | Risk | Response Strategy | Timeline | Responsible Party |
|---|---|---|---|---|
| High | [Risk] | Mitigate / Avoid / Transfer | [Timeline] | [Party] |
| Medium | [Risk] | Mitigate / Monitor | [Timeline] | [Party] |
| Low | [Risk] | Accept / Monitor | [Timeline] | [Party] |

---

## Monitoring and Review

- **Review Frequency:** Quarterly for High risks; Annually for Medium/Low
- **Trigger for Immediate Review:** Any new inspection, flood event, FEMA declaration, or regulatory action affecting listed assets
- **Next Scheduled Review:** [Date]

---

*Generated by InfraAdvisor AI | Data sources: {{ if context.bridges && context.bridges.size > 0 }}FHWA NBI{{ end }}{{ if context.water_systems && context.water_systems.size > 0 }} EPA SDWIS{{ end }}{{ if context.disaster_declarations && context.disaster_declarations.size > 0 }} OpenFEMA{{ end }} | {{ generated_date | default.if_empty "[Date]" }}*
