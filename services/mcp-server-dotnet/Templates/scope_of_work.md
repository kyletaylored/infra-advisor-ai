# Scope of Work
## {{ project_name | default.if_empty "Infrastructure Project" }}

**Prepared for:** {{ client_name | default.if_empty "[Client Name]" }}
**Prepared by:** [Firm Name]
**Date:** {{ generated_date | default.if_empty "[Date]" }}
**Document Type:** Scope of Work — {{ context.asset_type | default.if_empty "Infrastructure" }}

---

## 1. Project Background

{{ if context.bridges && context.bridges.size > 0 }}
This scope of work addresses inspection and assessment of **{{ context.bridges.size }}** bridge structure(s) identified in the National Bridge Inventory with conditions requiring professional evaluation.

**Structures included:**
{{ for bridge in context.bridges | array.limit 5 }}
- **{{ bridge.STRUCTURE_NUMBER_008 | default.if_empty "[Structure #]" }}** — {{ bridge.FACILITY_CARRIED_007 | default.if_empty "[Facility]" }}, {{ bridge.LOCATION_009 | default.if_empty "[Location]" }}
  - Deck Condition: {{ bridge.deck_condition | default.if_empty bridge.DECK_COND_058 | default.if_empty "N/A" }}
  - Last Inspection: {{ bridge.last_inspection_date | default.if_empty bridge.INSPECT_DATE_090 | default.if_empty "N/A" }}
{{ end }}
{{ else if context.water_systems && context.water_systems.size > 0 }}
This scope of work addresses water infrastructure assessment for **{{ context.water_systems.size }}** system(s) with identified compliance or capital improvement needs.
{{ else if context.project_description }}
{{ context.project_description }}
{{ else }}
[Insert project background and context here. Describe the infrastructure assets, their condition, and the need that drives this engagement.]
{{ end }}

{{ if notes }}
**Additional context:** {{ notes }}
{{ end }}

---

## 2. Scope of Services

### 2.1 Phase 1 — Data Collection and Records Review

- Obtain and review all available as-built drawings, prior inspection reports, maintenance records, and relevant agency correspondence
- Collect current condition data from FHWA NBI, FEMA disaster declarations, and EPA SDWIS as applicable
- Identify regulatory requirements and permit obligations

### 2.2 Phase 2 — Field Investigation

- Conduct field inspections in accordance with applicable standards:
  {{ if context.asset_type == "bridge" || (context.bridges && context.bridges.size > 0) }}
  - AASHTO/FHWA bridge inspection guidelines (FHWA Bridge Inspector's Reference Manual)
  - Biennial routine inspection; supplemental underwater/fracture-critical inspection as warranted
  {{ else if context.asset_type == "water" || (context.water_systems && context.water_systems.size > 0) }}
  - AWWA asset inspection standards
  - TCEQ inspection protocols for public water systems
  {{ else }}
  - Industry-standard inspection methodologies appropriate to asset type
  {{ end }}
- Document all findings with photographs, measurements, and condition ratings
- Collect material samples for laboratory testing where required

### 2.3 Phase 3 — Engineering Analysis

- Perform structural or systems analysis based on field findings
- Evaluate compliance with current design standards:
  {{ if context.bridges && context.bridges.size > 0 }}
  - AASHTO LRFD Bridge Design Specifications (current edition)
  - ASCE 7 load requirements
  - FHWA scour evaluation per HEC-18 methodology
  {{ else if context.water_systems && context.water_systems.size > 0 }}
  - EPA Safe Drinking Water Act regulations
  - TCEQ Chapter 290 design standards
  - AWWA system performance benchmarks
  {{ else }}
  - Applicable local, state, and federal design standards
  {{ end }}
- Develop load rating or capacity analysis as required
- Identify deficiencies and prioritize by risk (safety, regulatory, operational)

### 2.4 Phase 4 — Recommendations and Report

- Develop improvement alternatives with preliminary cost estimates
- Prepare prioritized capital improvement program
- Draft final report with executive summary suitable for agency/board presentation
- Identify applicable funding programs (IIJA, FEMA, TWDB SWIFT, CWSRF/DWSRF as applicable)

---

## 3. Deliverables

| Deliverable | Format | Due Date |
|---|---|---|
| Kickoff Meeting Agenda/Notes | PDF | [Date + 2 weeks] |
| Data Collection Summary | PDF/Excel | [Date + 6 weeks] |
| Field Inspection Report | PDF with photos | [Date + 12 weeks] |
| Engineering Analysis Technical Memo | PDF | [Date + 16 weeks] |
| Draft Final Report | PDF | [Date + 20 weeks] |
| Final Report (incorporating review comments) | PDF | [Date + 24 weeks] |
| GIS/Asset Database Update (if applicable) | Shapefile/GDB | [Date + 24 weeks] |

---

## 4. Project Schedule

| Phase | Duration | Start | End |
|---|---|---|---|
| Phase 1 — Records Review | 4 weeks | [NTP Date] | [+4 weeks] |
| Phase 2 — Field Investigation | 4 weeks | [+4 weeks] | [+8 weeks] |
| Phase 3 — Engineering Analysis | 6 weeks | [+8 weeks] | [+14 weeks] |
| Phase 4 — Report | 6 weeks | [+14 weeks] | [+20 weeks] |
| Client Review & Revisions | 4 weeks | [+20 weeks] | [+24 weeks] |

*Schedule subject to revision based on NTP date and client review cycles.*

---

## 5. Client Responsibilities

- Provide access to all existing records, drawings, and reports within 2 weeks of NTP
- Facilitate access to all project sites for field investigations
- Assign a project manager with authority to approve deliverables
- Provide review comments on draft report within 3 weeks of submittal

---

## 6. Exclusions

The following are specifically excluded from this scope unless separately authorized:

- Detailed design or construction documents
- Permitting services beyond consultation
- Right-of-way or easement acquisition
- Environmental Phase I or Phase II assessments (unless separately scoped)
- Construction management or inspection services

---

## 7. Preliminary Cost Estimate

| Service Phase | Estimated Fee |
|---|---|
| Phase 1 — Data Collection & Records Review | $[XX,XXX] |
| Phase 2 — Field Investigation | $[XX,XXX] |
| Phase 3 — Engineering Analysis | $[XX,XXX] |
| Phase 4 — Report Preparation | $[XX,XXX] |
| **Total Estimated Fee** | **$[XXX,XXX]** |
| Reimbursable Expenses (estimated) | $[X,XXX] |

*This is a preliminary estimate. A detailed fee proposal will be submitted upon project authorization.*

---

*Generated by InfraAdvisor AI | Data sources: {{ if context.bridges && context.bridges.size > 0 }}FHWA NBI{{ end }}{{ if context.water_systems && context.water_systems.size > 0 }} EPA SDWIS{{ end }} | {{ generated_date | default.if_empty "[Date]" }}*
