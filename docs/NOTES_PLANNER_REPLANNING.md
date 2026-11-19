# InternIQ Task Notes: Multi-Source Search Planner & Dynamic Query Formulator

**Author:** Samriddhi Raj
**Role:** Agent Core & Interface Engineer (Team HumanBot)
**Date:** November 2026
**Status:** Work Item 1 & Work Item 2 Completed, Tested (`20/20 PASSED`), Full Suite Clean (`71/71 PASSED`)

---

## 1. Overview & Architecture Role

The **Multi-Source Search Planner** (`agent/planner.py`) owns the planning and observation evaluation stages of the InternIQ agentic loop:

$$\text{Perceive} \longrightarrow \mathbf{\text{Plan}} \longrightarrow \text{Act} \longrightarrow \mathbf{\text{Observe (Evaluate)}} \longrightarrow \text{Vet} \longrightarrow \text{Rank} \longrightarrow \text{Generate} \longrightarrow \text{Approve} \longrightarrow \text{Record}$$

The planner has two complementary responsibilities across the initial search cycle:
1. **Planning (`create_plan`):** Generates structured search plans (`SearchPlan`) and dynamic query variations grounded in the student's verified `Profile` and target `Preference`.
2. **Observation Evaluation (`evaluate_observations`):** Evaluates the raw opportunities returned by ingestion connectors (`tools/fetchers/`), determines whether results are sufficient to continue downstream vetting, and diagnoses root causes of insufficiency.

---

## 2. Work Item 1: Implemented Planning Features

### A. Dynamic Search Query Formulation
Instead of searching for a single static string, the planner constructs three complementary query tiers:
1. **Primary Target Role Queries:** Exact job titles specified by the student (e.g., `"Machine Learning Intern"`).
2. **Domain-Specialized Queries:** Contextual pairings combining preferred domains and roles (e.g., `"FinTech Machine Learning Intern"`), while suppressing redundant repeats (e.g., preventing `"AI AI Engineer Intern"`).
3. **Skill-Focused Queries:** Targeted queries using verified technical skills (e.g., `"PyTorch Intern"`, `"Python Intern"`).

### B. Low-Signal Tool Filtering
Generic utility tools (e.g., `git`, `github`, `linux`, `bash`, `shell`, `docker`, `jira`, `ci/cd`, `rest api`) are filtered out from standalone title generation to prevent unhelpful queries like `"Git Intern"` or `"Linux Intern"`.

### C. Case-Insensitive Deduplication
All query strings and atomic keywords are normalized and deduplicated case-insensitively, preventing duplicate fetcher requests.

### D. Safe Input Validation & Edge Case Handling
* **Empty / Blank Target Roles:** Defaults gracefully to `["Software Engineering Intern"]`.
* **Missing Domains or Locations:** Cleanly handled as empty lists without crashing.
* **Missing or None Profile:** Successfully generates role-based queries and sets empty graduation year filters.
* **Safe Graduation Year Extraction:** Safely inspects candidate education items for integer graduation years.
* **Attempt Clamping:** Bounded strictly between $[1, 3]$ per Section 10.6 execution limits.

### E. Backward & Forward API Compatibility
The planner supports all existing calling patterns without breaking changes:
* `planner.create_plan(preference)`
* `planner.create_plan(preference, attempt=2)`
* `planner.create_plan(preference, 2)` (positional integer attempt)
* `planner.create_plan(preference, profile=profile)`
* `planner.create_plan(preference, profile)` (positional Profile)
* `planner.create_plan(preference, profile, 2)`
* `planner.create_plan(preference, 2, profile)`
* `planner.create_plan(preference, profile=profile, attempt=2)`

---

## 3. Work Item 2: Observation Evaluation & Insufficiency Diagnosis

### A. Why Observation Evaluation is Needed
In the agent workflow, after search queries are executed by fetchers (Greenhouse, Lever, CSV), the agent receives raw internship listings (observations). Without evaluation, the agent cannot tell whether:
* Opportunities actually match the student's field of study or target titles.
* Results meet location or remote work preferences.
* Enough high-quality listings exist to proceed to vetting and ranking.
* Search queries failed because they were too narrow, or because sources were exhausted.

Observation evaluation bridges **Act** and **Vet**, serving as the decision gate for the future LangGraph workflow.

### B. Structured Evaluation Model (`ObservationEvaluation`)
Evaluations produce a strongly typed Pydantic model:

```python
class ObservationEvaluation(BaseModel):
    is_sufficient: bool            # True if relevant listings meet min_sufficient_count
    total_observations: int       # Total raw listings retrieved (alias: total_listings)
    relevant_observations: int    # Listings passing role, domain, location checks (alias: relevant_listings)
    reasons: List[str]            # Itemized insufficiency explanations
    diagnosis: str                # Natural-language summary of findings
    recommend_replan: bool        # True if results insufficient AND attempt < 3 (alias: should_replan)
    relevant_listing_ids: List[str] # Listing IDs of qualifying opportunities
    unusable_count: int           # Count of malformed listings missing title/ID
    role_match_count: int         # Listings matching target roles
    domain_match_count: int       # Listings matching preferred domains
    location_match_count: int     # Listings matching preferred locations/work modes
```

### C. Relevance Determination Logic
Each observation is evaluated across four transparent dimensions:
1. **Usability & Schema Integrity:**
   * Validates non-empty `title` and `listing_id`.
   * Accepts both `Listing` Pydantic models and raw dictionaries from connectors.
   * Malformed items are tallied under `unusable_count` and disqualified.
2. **Role Matching:**
   * Checks listing title against `preference.target_roles`.
   * Evaluates exact phrases, non-generic role tokens (ignoring `"intern"`, `"junior"`), and standard tech abbreviations (`"ml"`, `"ai"`, `"ui"`, `"backend"`).
3. **Domain Matching:**
   * **Schema Note:** `Listing` does not have a dedicated `domain` attribute.
   * The planner inspects `title`, `description`, and `required_skills`/`preferred_skills` for domain signals (e.g., FinTech, Healthcare, EdTech, Cloud, Cybersecurity).
   * Short abbreviations (e.g., `"ai"`, `"ml"`) use word boundaries (`\b`) to avoid false substring matches.
4. **Location & Work Mode Matching:**
   * Matches remote listings when candidate accepts `WorkMode.REMOTE`.
   * Matches candidate physical locations (e.g., `"Bangalore"` / `"Bengaluru"`).
   * **Defensive Rule:** If a student requires physical locations, listings with missing location data (`locations=[]`, `work_mode=UNKNOWN`) are **not** silently treated as matches.

### D. Insufficiency Root Cause Diagnosis
When `is_sufficient` is `False`, the evaluation diagnoses the exact failure causes:
* **No Search Results:** Connectors returned 0 listings across all sources.
* **Malformed Listings:** Observations missing essential titles or IDs.
* **No Matching Roles:** Role titles retrieved do not match target career preferences.
* **No Matching Domains:** Opportunities are outside student's preferred industry domains.
* **Location Mismatch:** Listings located in non-preferred cities or incompatible work modes.
* **Too Few Relevant Listings:** Some listings matched, but total count is below `min_sufficient_count` (default: 3).

### E. Re-planning Recommendation & Budget Cap
* **Attempts 1 & 2:** When results are insufficient, `recommend_replan = True`.
* **Attempt 3 (Budget Cap):** Enforces Section 10.6 execution bounds (`max_replan_rounds = 3`). When attempt reaches 3, `recommend_replan = False`, and the diagnosis explicitly advises proceeding with available results.

---

## 4. Usage Examples

### Example 1: Creating an Initial Search Plan (Work Item 1)
```python
from agent.planner import SearchPlanner
from tools.schemas import Preference, Profile, Education, WorkMode

planner = SearchPlanner()
pref = Preference(
    profile_id="p1",
    target_roles=["Machine Learning Intern"],
    domains=["FinTech"],
    locations=["Bangalore"],
    work_modes=[WorkMode.REMOTE, WorkMode.HYBRID],
)
prof = Profile(
    profile_id="p1",
    full_name="Samriddhi Raj",
    email="samriddhi@example.edu",
    education=[Education(degree="B.Tech CSE", institution="S-VYASA", graduation_year=2026)],
    skills=["Python", "PyTorch", "SQL"],
)

plan = planner.create_plan(pref, prof)
print(plan["search_queries"])
# ['Machine Learning Intern', 'FinTech Machine Learning Intern', 'PyTorch Intern', 'Python Intern', 'SQL Intern']
```

### Example 2: Evaluating Search Observations (Work Item 2)
```python
from tools.schemas import Listing

listings = [
    Listing(
        listing_id="job_001",
        source_id="greenhouse",
        source_record_id="101",
        canonical_url="https://boards.greenhouse.io/job/101",
        title="Machine Learning Intern",
        organization="FinCorp",
        description="Developing financial fraud detection models.",
        locations=["Bangalore"],
        work_mode=WorkMode.HYBRID,
    )
]

evaluation = planner.evaluate_observations(
    observations=listings,
    preference=pref,
    profile=prof,
    attempt=1,
    min_sufficient_count=3,
)

print(f"Sufficient: {evaluation.is_sufficient}")          # False (found 1, requires 3)
print(f"Should Replan: {evaluation.recommend_replan}")    # True
print(f"Diagnosis: {evaluation.diagnosis}")
# Observations are insufficient (1/1 relevant; required 3). Primary factors: Too few relevant listings found...
```

---

## 5. Scope Distinctions: Current vs Future Functionality

| Component | Status | Description |
| :--- | :---: | :--- |
| **Dynamic Query Generation** | **Implemented (Work Item 1)** | Builds role, domain, and skill queries with tool filtering and deduplication. |
| **Structured SearchPlan Model** | **Implemented (Work Item 1)** | Typed schema with filters, target sources, and attempt tracking. |
| **Backward Compatible API** | **Implemented (Work Item 1)** | Supports positional and keyword arguments for `preference`, `attempt`, and `profile`. |
| **Observation Evaluation** | **Implemented (Work Item 2)** | Evaluates listing count, role/domain/location relevance, and unusable listings. |
| **Insufficiency Diagnosis** | **Implemented (Work Item 2)** | Identifies specific failure reasons and recommends re-planning within bounds. |
| **Progressive Constraint Relaxation** | *Pending (Work Item 3)* | Stepwise 3-round relaxation (broadening roles, expanding locations, fallback sources). |
| **LangGraph Node Wiring** | *Pending (Milestone 3)* | Integrating `plan_node` and conditional re-planning edges into `agent/graph.py`. |

> [!IMPORTANT]
> **Actual progressive constraint relaxation is NOT implemented yet.** Work Item 2 evaluates observation quality and determines whether re-planning is recommended; actual relaxation of constraints across attempts will be implemented in Work Item 3.

---

## 6. Verification Evidence

### Planner Unit Tests (`tests/unit/test_planner.py`):
```bash
.venv/bin/pytest tests/unit/test_planner.py -v
```

```text
============================= test session starts ==============================
collected 20 items

tests/unit/test_planner.py::test_role_based_query_generation PASSED      [  5%]
tests/unit/test_planner.py::test_domain_specialized_queries PASSED       [ 10%]
tests/unit/test_planner.py::test_skill_based_queries_and_tool_filtering PASSED [ 15%]
tests/unit/test_planner.py::test_duplicate_removal PASSED                [ 20%]
tests/unit/test_planner.py::test_missing_or_empty_profile_data PASSED    [ 25%]
tests/unit/test_planner.py::test_missing_or_empty_preferences PASSED     [ 30%]
tests/unit/test_planner.py::test_filter_extraction PASSED                [ 35%]
tests/unit/test_planner.py::test_attempt_boundaries PASSED               [ 40%]
tests/unit/test_planner.py::test_backward_compatibility_api PASSED       [ 45%]
tests/unit/test_planner.py::test_type_validation_errors PASSED           [ 50%]
tests/unit/test_planner.py::test_evaluation_sufficient_results PASSED    [ 55%]
tests/unit/test_planner.py::test_evaluation_no_search_results PASSED     [ 60%]
tests/unit/test_planner.py::test_evaluation_too_few_results PASSED       [ 65%]
tests/unit/test_planner.py::test_evaluation_role_mismatches PASSED       [ 70%]
tests/unit/test_planner.py::test_evaluation_domain_mismatches PASSED     [ 75%]
tests/unit/test_planner.py::test_evaluation_location_mismatches PASSED   [ 80%]
tests/unit/test_planner.py::test_evaluation_missing_or_incomplete_listing_fields PASSED [ 85%]
tests/unit/test_planner.py::test_evaluation_diagnosis_and_replanning_recommendation_bounds PASSED [ 90%]
tests/unit/test_planner.py::test_evaluation_with_dict_and_model_listings PASSED [ 95%]
tests/unit/test_planner.py::test_evaluation_type_validation_errors PASSED [100%]

======================== 20 passed, 22 warnings in 0.08s ========================
VERDICT: PASS
```

### Full Unit Test Suite (71 Tests, Zero Regressions):
```bash
.venv/bin/pytest tests/unit/ -v
# Result: 71 passed, 110 warnings in 0.84s
```
