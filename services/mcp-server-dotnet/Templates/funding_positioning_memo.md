# Funding Positioning Memo
## {{ project_name | default.if_empty "Water Infrastructure Project" }}

**Prepared for:** {{ client_name | default.if_empty "[Client Name]" }}
**Date:** {{ generated_date | default.if_empty "[Date]" }}
**Subject:** Funding Program Fit Assessment and Positioning Strategy

---

## Executive Summary

This memo assesses the fit of potential funding programs for {{ project_name | default.if_empty "the proposed project" }} and provides positioning guidance for the most promising application pathways.

{{ if context.water_plan_projects && context.water_plan_projects.size > 0 }}
**TWDB Water Plan Context:** {{ context.water_plan_projects.size }} recommended water supply strategy/strategies identified in the TWDB 2026 State Water Plan are relevant to this project. The $174B statewide water supply funding gap identified in the 2026 plan creates strong legislative and regulatory support for projects of this type.
{{ end }}

{{ if context.water_systems && context.water_systems.size > 0 }}
**Compliance Driver:** {{ context.water_systems.size }} public water system(s) with identified compliance issues or infrastructure needs, creating urgency and regulatory compliance motivation for funding applications.
{{ end }}

{{ if notes }}
**Analyst Notes:** {{ notes }}
{{ end }}

---

## Funding Program Assessment

### Program 1: TWDB State Water Implementation Fund for Texas (SWIFT)

**Program Type:** Low-interest loan (not a grant)
**Interest Rate:** Approximately 2% below prevailing market rate
**Maximum Term:** 30 years
**Available Volume:** Up to $4 billion per biennium (Texas Constitution Article III, Section 49-d-8)

#### Eligibility Checklist

| Requirement | Status | Notes |
|---|---|---|
| Project is a "recommended water management strategy" in adopted TWDB Regional Water Plan | ☐ Confirm | Must be in the region's adopted plan (Regions A–P) |
| Project sponsor is a political subdivision of Texas | ☐ Confirm | Municipality, district, county, authority |
| Project is in the public interest and promotes conservation | ☐ Confirm | |
| Water Conservation Plan filed with TWDB | ☐ Required | Per 31 TAC §363.15 |
| Water Loss Audit submitted (within past 12 months) | ☐ Required | Per SB 3 (2011) requirements |
| Asset Management Plan adopted | ☐ Strongly recommended | Required for 2026 cycle priority scoring |
| Drought Contingency Plan filed with TCEQ | ☐ Required | Per 30 TAC §288 |
| Financial sustainability analysis | ☐ Required | 3–5 years projected financials |

#### Application Requirements

1. Letter of Intent (LOI) — submitted during TWDB application window (typically Jan–March biennially)
2. Preliminary Engineering Report (PER) — TWDB Engineering Design Standard compliance
3. Environmental Information Document (EID) — for projects requiring environmental review
4. Financial Statements — 3 years audited financials + 5-year pro forma
5. Water Conservation Plan — filed with TWDB
6. Water Loss Audit — filed within prior 12 months
7. Asset Management Plan — documented system inventory, condition assessment, CIP
8. Board Resolution — authorizing application and committing to project

#### Positioning Strategy

{{ if context.water_plan_projects && context.water_plan_projects.size > 0 }}
**Strong positioning:** The project aligns with TWDB 2026 State Water Plan recommended strategies. Emphasize:
- Specific regional water plan strategy reference (Region {{ context.water_plan_projects[0].planning_region | default.if_empty "[X]" }})
- Supply volume to be added (acre-feet/year)
- Number of water user groups served
- Connection to the $174B identified funding gap
{{ else }}
**Positioning considerations:**
- Document alignment with the applicable TWDB Regional Water Plan (Regions A–P)
- Quantify water supply benefit in acre-feet/year
- Demonstrate need relative to projected 2030–2080 demand growth
{{ end }}

---

### Program 2: EPA Drinking Water State Revolving Fund (DWSRF) — IIJA Enhanced

**Program Type:** Low-interest loan; up to 100% principal forgiveness for disadvantaged communities
**Administered by:** Texas Water Development Board (on behalf of EPA)
**IIJA Supplemental Funding:** $15B nationally through 2026 (lead service line replacement, PFAS)

#### Eligibility Checklist

| Requirement | Status | Notes |
|---|---|---|
| Project improves a public water system serving community | ☐ Confirm | Community water systems (CWS) priority |
| Project addresses health-based violation or risk | ☐ Confirm | SDWA compliance driver strengthens application |
| System serves fewer than 10,000 persons (for principal forgiveness priority) | ☐ Confirm | Disadvantaged community criteria |
| Environmental review completed (NEPA categorical exclusion or EA) | ☐ Required | |
| Disadvantaged community determination submitted | ☐ If applicable | Enables 0% loan / principal forgiveness |
| System has current sanitary survey (within 3 years) | ☐ Required | |
| Lead service line inventory submitted | ☐ Required (IIJA) | All public water systems |

#### Positioning Strategy

{{ if context.water_systems && context.water_systems.size > 0 }}
**Compliance-driven positioning:** Systems with open SDWA violations present a compelling case for DWSRF funding. Key messages:
- Regulatory compliance urgency (EPA/TCEQ enforcement risk)
- Public health protection for {{ context.water_systems[0].population_served | default.if_empty context.water_systems[0].POPULATION_SERVED_COUNT | default.if_empty "[N]" }}+ residents
- Lead service line replacement opportunity (IIJA priority)
{{ else }}
- Frame project as proactive compliance and public health protection
- If serving disadvantaged community, document median household income data for principal forgiveness application
- Align with EPA's national PFAS and lead service line replacement priorities
{{ end }}

---

### Program 3: EPA Clean Water State Revolving Fund (CWSRF)

**Program Type:** Low-interest loan; principal forgiveness for disadvantaged communities
**Applies to:** Wastewater, stormwater, nonpoint source, and water efficiency projects

#### Eligibility Checklist

| Requirement | Status | Notes |
|---|---|---|
| Project addresses water quality or wastewater treatment | ☐ Confirm | |
| Green Project Reserve (GPR) component (20% of grant) | ☐ Plan for | Water efficiency, green infrastructure eligible |
| NEPA environmental review | ☐ Required | |
| Wage rate compliance (Davis-Bacon) | ☐ Required | All SRF-funded construction |

---

### Program 4: FEMA Building Resilient Infrastructure and Communities (BRIC)

**Program Type:** Grant (75% federal / 25% non-federal cost share)
**Applies to:** Hazard mitigation projects that reduce risk to critical infrastructure

#### Eligibility Checklist

| Requirement | Status | Notes |
|---|---|---|
| Project reduces risk from natural hazard (flood, hurricane, drought) | ☐ Confirm | |
| Located in FEMA-designated hazard area OR addresses disaster risk | ☐ Confirm | |
| Benefit-Cost Analysis (BCA) ratio ≥ 1.0 | ☐ Required | FEMA BCA Tool v6 |
| Project is in State/Local Hazard Mitigation Plan | ☐ Required | Must be in adopted SHMP or LHMP |
| 25% non-federal cost share committed | ☐ Required | Can be in-kind |
| Community Lifelines criteria addressed | ☐ Preferred | Safety & Security, Water Systems |

{{ if context.disaster_declarations && context.disaster_declarations.size > 0 }}
**Disaster history context:** {{ context.disaster_declarations.size }} FEMA disaster declaration(s) affecting this area provide strong justification for hazard mitigation investment. Document repeat events to strengthen BCA.
{{ end }}

---

### Program 5: IIJA Water Infrastructure Finance (WIFIA)

**Program Type:** Low-interest loan (not a grant)
**Minimum Project Size:** $5 million
**Administered by:** EPA directly
**Advantage:** Very long terms (up to 35 years), interest rate = US Treasury rate

#### Eligibility Checklist

| Requirement | Status | Notes |
|---|---|---|
| Project cost ≥ $5M | ☐ Confirm | |
| Credit-worthy borrower | ☐ Confirm | Investment-grade rating or equivalent |
| Environmental review complete | ☐ Required | NEPA EA or CE |
| Letter of Interest (LOI) submitted during annual WIFIA solicitation | ☐ Required | |

---

## Recommended Funding Strategy

| Priority | Program | Application Type | Estimated Coverage | Timeline |
|---|---|---|---|---|
| 1st | TWDB SWIFT | Low-interest loan | Up to 100% of project cost | Next TWDB application cycle |
| 2nd | EPA DWSRF (IIJA) | Loan ± principal forgiveness | Up to 100% for disadvantaged communities | Annual TWDB-administered cycle |
| 3rd | FEMA BRIC | 75% federal grant | Up to 75% of eligible costs | Annual FEMA BRIC application |
| 4th | WIFIA | Direct EPA loan | Up to 80% of project costs | Annual EPA solicitation |

**Recommended immediate actions:**
1. Confirm project alignment with applicable TWDB Regional Water Plan strategy
2. File Water Conservation Plan and Water Loss Audit with TWDB if not current
3. Develop Asset Management Plan to strengthen SWIFT scoring
4. Engage TWDB regional representative to discuss pre-application positioning
5. Initiate BCA analysis for BRIC application parallel track

---

## Required Documentation Timeline

| Document | Purpose | Responsible | Target Date |
|---|---|---|---|
| Water Conservation Plan | SWIFT + DWSRF requirement | Utility | [Date] |
| Water Loss Audit | SWIFT requirement | Utility | [Date] |
| Asset Management Plan | SWIFT scoring + DWSRF | Consultant | [Date] |
| Preliminary Engineering Report | SWIFT pre-application | Consultant | [Date] |
| Environmental Information Document | SWIFT + DWSRF | Consultant | [Date] |
| Benefit-Cost Analysis | FEMA BRIC | Consultant | [Date] |
| TWDB Letter of Intent | SWIFT application trigger | Utility + Consultant | Per TWDB cycle |

---

*Generated by InfraAdvisor AI | Data sources: {{ if context.water_plan_projects && context.water_plan_projects.size > 0 }}TWDB 2026 State Water Plan{{ end }}{{ if context.water_systems && context.water_systems.size > 0 }} EPA SDWIS{{ end }} | {{ generated_date | default.if_empty "[Date]" }}*
*Reference: TWDB SWIFT Program — twdb.texas.gov/financial/programs/SWIFT | EPA SRF — epa.gov/cwsrf | FEMA BRIC — fema.gov/grants/mitigation/bric*
