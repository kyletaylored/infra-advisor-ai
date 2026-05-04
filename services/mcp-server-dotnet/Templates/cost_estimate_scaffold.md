# Cost Estimate Scaffold
## {{ project_name | default.if_empty "Infrastructure Project" }}

**Prepared for:** {{ client_name | default.if_empty "[Client Name]" }}
**Date:** {{ generated_date | default.if_empty "[Date]" }}
**Estimate Class:** Class 5 — Order of Magnitude (±50%)
**Basis:** Preliminary assessment data; detailed design required for Class 3+ estimate

> ⚠️ **Disclaimer:** This is a planning-level cost estimate based on available condition data and regional cost indices. It is intended for budgeting and funding application purposes only. Costs should be refined through detailed engineering analysis, site-specific investigation, and competitive bidding.

---

## Project Summary

{{ if context.bridges && context.bridges.size > 0 }}
**Asset Type:** Bridge Rehabilitation / Replacement
**Structures:** {{ context.bridges.size }}

| Structure # | Location | Lowest Rating | Recommended Action |
|---|---|---|---|
{{ for bridge in context.bridges }}
| {{ bridge.STRUCTURE_NUMBER_008 | default.if_empty "N/A" }} | {{ bridge.FACILITY_CARRIED_007 | default.if_empty "N/A" }} | {{ bridge.lowest_rating | default.if_empty "N/A" }} | {{ if bridge.lowest_rating && bridge.lowest_rating < 3 }}Replacement{{ else if bridge.lowest_rating && bridge.lowest_rating < 5 }}Major Rehabilitation{{ else }}Rehabilitation{{ end }} |
{{ end }}
{{ else if context.water_systems && context.water_systems.size > 0 }}
**Asset Type:** Water Infrastructure Improvement
**Systems:** {{ context.water_systems.size }}
{{ else }}
**Asset Type:** {{ context.asset_type | default.if_empty "[Infrastructure Type]" }}
{{ end }}

{{ if notes }}
**Notes:** {{ notes }}
{{ end }}

---

## Cost Estimate by Phase

### Phase 1 — Preliminary Engineering (5–8% of Construction Cost)

| Item | Unit | Quantity | Unit Cost | Extended Cost |
|---|---|---|---|---|
| Project management | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Data collection and records review | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Field investigation / inspection | EA | [N] | $[X,XXX] | $[XX,XXX] |
| Topographic survey | LF | [N] | $[XX] | $[XX,XXX] |
| Geotechnical investigation | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Preliminary engineering report | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| **Phase 1 Subtotal** | | | | **$[XXX,XXX]** |

### Phase 2 — Final Design (8–12% of Construction Cost)

| Item | Unit | Quantity | Unit Cost | Extended Cost |
|---|---|---|---|---|
| Project management | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Final design — civil/structural | LS | 1 | $[XXX,XXX] | $[XXX,XXX] |
| Environmental documentation | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Permitting support | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Plans, specs, and estimate | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Bid advertisement support | LS | 1 | $[X,XXX] | $[X,XXX] |
| **Phase 2 Subtotal** | | | | **$[XXX,XXX]** |

### Phase 3 — Construction

{{ if context.bridges && context.bridges.size > 0 }}
#### Bridge Rehabilitation Line Items

| Item | Unit | Quantity | Unit Cost | Extended Cost |
|---|---|---|---|---|
| Mobilization / demobilization (5%) | LS | 1 | — | $[XXX,XXX] |
| Traffic control | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Deck removal and replacement | SF | [N] | $[XXX] | $[XXX,XXX] |
| Superstructure repair / painting | LF | [N] | $[XXX] | $[XXX,XXX] |
| Expansion joint replacement | LF | [N] | $[XXX] | $[XX,XXX] |
| Bearing replacement | EA | [N] | $[X,XXX] | $[XX,XXX] |
| Substructure repair | LS | 1 | $[XXX,XXX] | $[XXX,XXX] |
| Scour countermeasures (riprap/sheet pile) | CY | [N] | $[XXX] | $[XX,XXX] |
| Approach pavement | SY | [N] | $[XXX] | $[XX,XXX] |
| Signing and delineation | LS | 1 | $[X,XXX] | $[X,XXX] |
| Contingency (15%) | LS | 1 | — | $[XXX,XXX] |
{{ else if context.water_systems && context.water_systems.size > 0 }}
#### Water Infrastructure Construction Line Items

| Item | Unit | Quantity | Unit Cost | Extended Cost |
|---|---|---|---|---|
| Mobilization / demobilization (5%) | LS | 1 | — | $[XXX,XXX] |
| Site preparation | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Process equipment supply and install | LS | 1 | $[XXX,XXX] | $[XXX,XXX] |
| Piping and valves | LF | [N] | $[XXX] | $[XXX,XXX] |
| Electrical and instrumentation | LS | 1 | $[XXX,XXX] | $[XXX,XXX] |
| SCADA / controls | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Building / enclosure | SF | [N] | $[XXX] | $[XXX,XXX] |
| Site work / paving / fencing | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Startup and commissioning | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Contingency (15%) | LS | 1 | — | $[XXX,XXX] |
{{ else }}
#### Construction Line Items

| Item | Unit | Quantity | Unit Cost | Extended Cost |
|---|---|---|---|---|
| Mobilization / demobilization | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| [Major work item 1] | [Unit] | [Qty] | $[Unit Cost] | $[Extended] |
| [Major work item 2] | [Unit] | [Qty] | $[Unit Cost] | $[Extended] |
| [Major work item 3] | [Unit] | [Qty] | $[Unit Cost] | $[Extended] |
| Contingency (15%) | LS | 1 | — | $[XXX,XXX] |
{{ end }}

| | | | | |
|---|---|---|---|---|
| **Phase 3 Construction Subtotal** | | | | **$[X,XXX,XXX]** |

### Phase 4 — Construction Management (8–10% of Construction)

| Item | Unit | Quantity | Unit Cost | Extended Cost |
|---|---|---|---|---|
| Construction inspection | MO | [N] | $[XX,XXX] | $[XXX,XXX] |
| Materials testing | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| Project closeout / record drawings | LS | 1 | $[XX,XXX] | $[XX,XXX] |
| **Phase 4 Subtotal** | | | | **$[XXX,XXX]** |

---

## Total Project Cost Summary

| Phase | Estimated Cost |
|---|---|
| Phase 1 — Preliminary Engineering | $[XXX,XXX] |
| Phase 2 — Final Design | $[XXX,XXX] |
| Phase 3 — Construction | $[X,XXX,XXX] |
| Phase 4 — Construction Management | $[XXX,XXX] |
| **Total Project Cost** | **$[X,XXX,XXX]** |
| Right-of-Way / Easements (if applicable) | $[XX,XXX] |
| **Total Program Cost** | **$[X,XXX,XXX]** |

---

## Regional Cost Factors

| Factor | Adjustment |
|---|---|
| Texas Gulf Coast urban (Houston/Corpus Christi area) | +15–20% |
| Texas rural / West Texas | −5 to +5% |
| Current market escalation (2025–2026) | +8–12% |
| Labor shortage premium (skilled trades) | +5–10% |

*Base costs from RS Means 2024, TXDOT pay item historical data, and TWDB construction cost indices.*

---

## Applicable Funding Programs

{{ if context.bridges && context.bridges.size > 0 }}
| Program | Max Federal Share | Notes |
|---|---|---|
| IIJA Highway Bridge Program | 80% federal | Structurally deficient priority |
| IIJA Bridge Investment Program | 80% federal | Spans >1,000 ft or major economic corridor |
| FEMA BRIC | 75% federal | Requires benefit-cost ratio ≥1.0 |
| TxDOT Off-System Bridge Program | 80% federal | For county roads off the NHS |
{{ else if context.water_systems && context.water_systems.size > 0 }}
| Program | Max Federal Share / Terms | Notes |
|---|---|---|
| TWDB SWIFT | ~2% below market rate, 30-yr | Must be in adopted TWDB Regional Water Plan |
| EPA DWSRF (IIJA) | Up to 100% (disadvantaged communities) | Lead service line / PFAS priority |
| EPA CWSRF | 49% principal forgiveness (disadvantaged) | Wastewater projects |
| USDA Water & Waste Grants | Up to 75% grant | Rural communities <10,000 population |
{{ else }}
| Program | Notes |
|---|---|
| IIJA Infrastructure Programs | Various federal programs depending on asset type |
| FEMA Hazard Mitigation | 75% federal for eligible mitigation projects |
| State Revolving Funds | TWDB SWIFT, CWSRF, DWSRF depending on project type |
{{ end }}

---

*Generated by InfraAdvisor AI | Estimate Class 5 (Order of Magnitude) | {{ generated_date | default.if_empty "[Date]" }}*
*Data sources: {{ if context.bridges && context.bridges.size > 0 }}FHWA NBI{{ end }}{{ if context.water_systems && context.water_systems.size > 0 }} EPA SDWIS{{ end }}*
