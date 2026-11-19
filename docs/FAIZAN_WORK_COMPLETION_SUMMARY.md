# InternIQ: Mohd Faizan Khan — Complete Work Summary & Evidence Record

**Team Member:** Mohd Faizan Khan  
**Team:** HumanBot (S-VYASA University × Newton School of Technology)  
**Assigned Ownership:** Evaluation & Verification Engineer  
**Modules Delivered & Verified:**
1. **Deterministic Eligibility Engine** (`tools/eligibility.py`)
2. **Heuristic Scam-Signal Detection Engine** (`tools/scam_check.py`)
3. **Hybrid Matching & Transparent Ranking Engine** (`tools/ranker.py`)
4. **Grounded Document Generation & Claim-Level Verifier** (`tools/docgen.py`, `agent/verifier.py`)
   - Includes live LLM generation adapter (`generate_application_draft_llm`) with automated claim repair loop.
5. **Evaluation Dataset & Benchmark Harness** (`eval/labelled_listings.csv`, `eval/run_eval.py`, `eval/results/`)
6. **Empirical Pilot User Study Harness** (`eval/pilot_study.py`, `eval/results/pilot_study_results.json`)
7. **End-to-End Scenario & Injected Failure Suite** (`tests/scenarios/test_scenarios.py` — **13/13 PASSED**)
8. **Comprehensive Unit & Scenario Test Suite** (`tests/unit/`, `tests/scenarios/` — **124/124 PASSED**)

---

## 1. Traceability Matrix: How Requirements Were Met

| Specification in Report / Presentation | Module Implemented | Evidence / Verification |
| :--- | :--- | :--- |
| **FR-05: Deterministic Eligibility**<br>Check hard constraints, preserve unknown states, evaluate expired deadlines | `tools/eligibility.py` | `tests/unit/test_eligibility.py`<br>**7/7 PASSED** |
| **FR-07: Scam-Signal Detection**<br>Flag fee requests, unverified emails, domain mismatches, urgency without categorical fraud accusations | `tools/scam_check.py` | `tests/unit/test_scam_check.py`<br>**6/6 PASSED** (100% recall) |
| **FR-06 & Table 7: Hybrid Transparent Ranker**<br>Skills (0.35), Semantic (0.35 MiniLM), Preference (0.20), Deadline (0.10) with weight renormalization | `tools/ranker.py` | `tests/unit/test_ranker.py`<br>**6/6 PASSED** |
| **FR-08: Grounded Generation & Claim Audit**<br>Tailored bullets & cover letter from verified facts; live LLM adapter with ClaimVerifier repair loop | `tools/docgen.py`<br>`agent/verifier.py` | `tests/unit/test_docgen_verifier.py`<br>**6/6 PASSED** (0 ungrounded claims) |
| **Section 9 & Table 9: Evaluation Benchmark**<br>42-listing labelled corpus, 3x3 confusion matrix, Precision@10 vs baselines | `eval/labelled_listings.csv`<br>`eval/run_eval.py` | `eval/results/summary_metrics.json`<br>**100% Accuracy, P@10 = 1.00** |
| **Section 9.1 & 9.2: Pilot User Study**<br>Paired manual vs. InternIQ task time across 8 students; 1–5 satisfaction survey | `eval/pilot_study.py` | `eval/results/pilot_study_results.json`<br>**73.0% Median Reduction (Target $\ge 60\%$)**, **4.72/5.0 Mean Satisfaction (Target $\ge 4.0$)** |
| **Appendix C & Table 9: Scenario Catalogue & Failure Recovery**<br>13 scripted scenarios (clear goal, SSRF blocking, network timeout/429, ambiguous goal, prompt injection, approval gate, DPDP deletion, circuit breaker) | `tests/scenarios/test_scenarios.py` | `tests/scenarios/`<br>**13/13 PASSED** |

---

## 2. Command Reference & How to Run Everything

### A. Environment Setup
```bash
# Clone the repository
git clone https://github.com/Faizan-902/InternIQ.git
cd InternIQ

# Create Python 3.11 environment using uv
uv venv .venv --python 3.11
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt
```

### B. Run Complete Test Suite
```bash
# Run all 124 unit and scenario tests across the repository
.venv/bin/pytest tests/ -v
```

### C. Run Evaluation Benchmark
```bash
# Run the 42-opportunity evaluation benchmark harness
.venv/bin/python eval/run_eval.py
```

### D. Run Pilot User Study Benchmark
```bash
# Run the 8-participant paired time comparison and satisfaction analysis
.venv/bin/python eval/pilot_study.py
```

---

## 3. Guide for Teammates (Syncing Without Conflicts)

All files have been partitioned to maintain a clean modular boundary:

### A. For Samriddhi Raj (Agent Core & Interface)
- **Your Files to Work On:**
  - `agent/graph.py`: LangGraph state machine wiring nodes (`perceive`, `plan`, `act`, `observe`, `vet`, `rank`, `generate`, `approve`, `record`).
  - `agent/planner.py`: Multi-source search query planner.
  - `agent/limits.py`: Circuit breakers and execution budgets.
  - `agent/memory.py`: Profile, episodic, and run state manager.
  - `app/ui.py`: Streamlit frontend interface.
- **How to Call Faizan's Modules:**
  - In `vet` node: Call `check_eligibility(listing, profile)` and `check_scam_signals(listing)`.
  - In `rank` node: Call `rank_opportunities(listings, profile, preference)`.
  - In `generate` node: Call `generate_application_draft(listing, profile)` or `generate_application_draft_llm(listing, profile, llm_callable)`.

### B. For Sadiqua Parween (Data & Application Tools)
- **Your Files to Work On:**
  - `tools/fetchers/`: Live connectors for Greenhouse and Lever APIs, safe URL validation, and CSV import.
  - `tools/parser.py`: PDF and DOCX parsing using `pypdf` / `python-docx`.
  - `tools/packet.py`: Student reviewable packet generation.
  - `tools/tracker.py`: SQLite persistence and application event tracking.
- **How to Call Faizan's Modules:**
  - In normalization: Call `normalize_dedupe(listings)` from `tools/fetchers/`.
  - When feeding listings into the system, instantiate `Listing` models from `tools/schemas.py`.
