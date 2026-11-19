# InternIQ Task Notes: Multi-Source Search Planner & Dynamic Query Formulator

**Author:** Samriddhi Raj
**Role:** Agent Core & Interface Engineer (Team HumanBot)
**Date:** November 2026
**Status:** Work Item 1 Completed, Tested (`10/10 PASSED`), Full Suite Clean (`61/61 PASSED`)

---

## 1. Overview & Architecture Role

The **Multi-Source Search Planner** (`agent/planner.py`) owns the planning stage (Node 2: Plan) of the InternIQ agentic loop:

$$\text{Perceive} \longrightarrow \mathbf{\text{Plan}} \longrightarrow \text{Act} \longrightarrow \text{Observe} \longrightarrow \text{Vet} \longrightarrow \text{Rank} \longrightarrow \text{Generate} \longrightarrow \text{Approve} \longrightarrow \text{Record}$$

In earlier iterations, `agent/planner.py` was an interface stub returning static mock queries (`"Software Engineering Intern"`). In **Work Item 1**, we replaced this placeholder with dynamic query generation grounded in the candidate's verified `Profile` and target `Preference`.

The planner produces structured `SearchPlan` specifications consumed directly by data ingestion connectors (`tools/fetchers/`):
* Authorized Greenhouse board queries
* Lever site postings queries
* Local CSV and benchmark fixture search filters

---

## 2. Work Item 1: Implemented Features

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

### E. 100% Backward and Forward API Compatibility
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

## 3. Data Model & Output Schema

The plan is returned as a JSON-serializable dictionary matching the `SearchPlan` Pydantic model:

```python
class SearchPlan(BaseModel):
    plan_id: str                      # Unique plan identifier, e.g. "plan_abc123_att1"
    attempt: int                      # Iteration attempt (1 to 3)
    target_sources: List[str]         # Default: ["greenhouse", "lever", "manual_csv"]
    query_keywords: List[str]         # Atomic keywords (roles, domains, top skills)
    search_queries: List[str]         # Formatted search query strings
    filters: Dict[str, Any]           # Constraints: locations, work modes, stipend, grad years
    relaxed_mode: bool                # False for initial plans; updated in Work Item 3 when relaxation is applied
    relaxation_notes: List[str]       # Audit trail of relaxation actions
```

---

## 4. Usage Examples

### Example 1: Basic Role Query Formulation (Original Stub Compatibility)
```python
from agent.planner import SearchPlanner
from tools.schemas import Preference, WorkMode

planner = SearchPlanner()
pref = Preference(
    profile_id="prof_001",
    target_roles=["Backend Engineering Intern"],
    locations=["Bangalore"],
    work_modes=[WorkMode.REMOTE],
)

plan = planner.create_plan(pref)
print("Queries:", plan["search_queries"])
# Output: Queries: ['Backend Engineering Intern']
```

### Example 2: Multi-Factor Query Formulation with Profile
```python
from tools.schemas import Profile, Education

prof = Profile(
    profile_id="prof_001",
    full_name="Samriddhi Raj",
    email="samriddhi@example.edu",
    education=[Education(degree="B.Tech CSE", institution="S-VYASA", graduation_year=2026)],
    skills=["Python", "PyTorch", "SQL", "Git"],
)

plan = planner.create_plan(pref, profile=prof)
print("Keywords:", plan["query_keywords"])
# Output: Keywords: ['Backend Engineering Intern', 'Python', 'PyTorch', 'SQL']
print("Queries: ", plan["search_queries"])
# Output: Queries:  ['Backend Engineering Intern', 'Python Intern', 'PyTorch Intern', 'SQL Intern']
print("Filters: ", plan["filters"])
# Output: Filters:  {'locations': ['Bangalore'], 'work_modes': ['remote'], 'min_stipend': None, 'target_graduation_years': [2026]}
```

---

## 5. Scope Distinctions: Current vs Future Functionality

To ensure modular delivery and prevent feature creep, responsibilities are clearly bounded:

| Component | Status | Description |
| :--- | :---: | :--- |
| **Dynamic Query Generation** | **Implemented (Work Item 1)** | Builds role, domain, and skill queries with tool filtering and deduplication. |
| **Structured SearchPlan Model** | **Implemented (Work Item 1)** | Typed schema with filters, target sources, and attempt tracking. |
| **Backward Compatible API** | **Implemented (Work Item 1)** | Supports positional and keyword arguments for `preference`, `attempt`, and `profile`. |
| **Observation Evaluation** | *Pending (Work Item 2)* | Analyzing fetch results, disqualification rates, and scam signals to detect insufficiency. |
| **Progressive Constraint Relaxation** | *Pending (Work Item 3)* | Stepwise 3-round relaxation (broadening roles, expanding locations, fallback sources). |
| **LangGraph Node Wiring** | *Pending (Milestone 3)* | Integrating `plan_node` and conditional re-planning edges into `agent/graph.py`. |

---

## 6. Verification Evidence

The implementation was validated using dedicated unit tests in `tests/unit/test_planner.py`:

```bash
.venv/bin/pytest tests/unit/test_planner.py -v
```

### Test Output:
```text
============================= test session starts ==============================
collected 10 items

tests/unit/test_planner.py::test_role_based_query_generation PASSED      [ 10%]
tests/unit/test_planner.py::test_domain_specialized_queries PASSED       [ 20%]
tests/unit/test_planner.py::test_skill_based_queries_and_tool_filtering PASSED [ 30%]
tests/unit/test_planner.py::test_duplicate_removal PASSED                [ 40%]
tests/unit/test_planner.py::test_missing_or_empty_profile_data PASSED    [ 50%]
tests/unit/test_planner.py::test_missing_or_empty_preferences PASSED     [ 60%]
tests/unit/test_planner.py::test_filter_extraction PASSED                [ 70%]
tests/unit/test_planner.py::test_attempt_boundaries PASSED               [ 80%]
tests/unit/test_planner.py::test_backward_compatibility_api PASSED       [ 90%]
tests/unit/test_planner.py::test_type_validation_errors PASSED           [100%]

======================== 10 passed, 5 warnings in 0.08s ========================
VERDICT: PASS
```

### Full Unit Test Suite (Zero Regressions):
```bash
.venv/bin/pytest tests/unit/ -v
# Result: 61 passed in 0.85s (10 planner + 10 memory + 6 tracker + 6 scam check + 7 eligibility + 8 fetchers + 4 parser + 6 ranker + 3 docgen + 1 packet)
```
