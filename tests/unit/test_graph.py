"""
Unit Tests for LangGraph Search-Observe-Replan Orchestration
Owner: Samriddhi Raj (Team HumanBot)
Domain: Agent Core & Interface

Covers Milestone 3 Work Item 1:
- Typed InternIQAgentState initialization and validation.
- perceive_node input validation, safe fallback, and validation routing.
- plan_node initial generation and progressive replanning (SearchPlan models & dicts).
- act_node search execution, connector safety, tool call accounting, and cumulative deduplication.
- observe_node evaluation integration.
- Conditional routing: sufficient results (no replan) vs insufficient results (replan).
- ExecutionBudget circuit breaker: exactly 3 replan rounds (4 total search attempts), Attempt 5 impossible.
- Safe handling of unconfigured or failed search sources.
"""

import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from tools.schemas import (
    Profile,
    Preference,
    Education,
    WorkMode,
    Listing,
)
from agent.limits import ExecutionBudget
from agent.planner import SearchPlanner, SearchPlan, ObservationEvaluation
from agent.graph import (
    InternIQAgentState,
    perceive_node,
    route_after_perceive,
    plan_node,
    act_node,
    observe_node,
    route_after_observe,
    build_search_loop_graph,
)


@pytest.fixture
def sample_profile():
    return Profile(
        profile_id="prof_graph_test",
        full_name="Samriddhi Raj",
        email="samriddhi@example.edu",
        education=[
            Education(
                degree="B.Tech Computer Science & Engineering",
                institution="S-VYASA University",
                graduation_year=2026,
                cgpa=9.1,
            )
        ],
        skills=["Python", "Machine Learning", "PyTorch", "Docker"],
        projects=[],
        confirmed_facts=[],
    )


@pytest.fixture
def sample_preference():
    return Preference(
        profile_id="prof_graph_test",
        target_roles=["Machine Learning Intern"],
        domains=["FinTech", "Healthcare"],
        locations=["Bangalore"],
        work_modes=[WorkMode.REMOTE, WorkMode.HYBRID],
        min_stipend=30000.0,
        constraints={"flexible_stipend": True},
    )


@pytest.fixture
def sample_csv_content():
    return (
        "title,organization,description,locations,work_mode,stipend\n"
        "Machine Learning Intern,FinTech Corp,Build predictive models in FinTech,Bangalore,remote,35000\n"
        "Machine Learning Intern,HealthAI Labs,Clinical NLP and ML models in Healthcare,Bangalore,hybrid,40000\n"
        "Machine Learning Intern,DataCorp,Deep learning and NLP systems in FinTech,Bangalore,remote,32000\n"
    )


# =====================================================================
# State Model Tests
# =====================================================================

def test_graph_state_model_defaults(sample_preference, sample_profile):
    """Verifies that InternIQAgentState initializes with proper defaults and custom fields."""
    state = InternIQAgentState(
        run_id="run_001",
        preference=sample_preference,
        profile=sample_profile,
    )
    assert state.run_id == "run_001"
    assert state.preference == sample_preference
    assert state.profile == sample_profile
    assert state.budget.max_replan_rounds == 3
    assert state.budget.current_replan_count == 0
    assert state.listings == []
    assert state.current_plan is None
    assert state.evaluation is None
    assert state.iteration == 1
    assert state.completion_status == "in_progress"


# =====================================================================
# Perceive Node & Validation Routing Tests
# =====================================================================

def test_perceive_node_valid(sample_preference, sample_profile):
    """Verifies perceive_node marks status as perceived when preference is valid."""
    state = InternIQAgentState(
        run_id="run_perceive_valid",
        preference=sample_preference,
        profile=sample_profile,
    )
    update = perceive_node(state)
    assert update["completion_status"] == "perceived"
    assert len(update["errors"]) == 0


def test_perceive_node_missing_preference(sample_profile):
    """Verifies perceive_node records an error when candidate preference is missing."""
    state = InternIQAgentState(
        run_id="run_perceive_invalid",
        preference=None,
        profile=sample_profile,
    )
    update = perceive_node(state)
    assert update["completion_status"] == "failed_validation"
    assert any("Candidate preference is missing" in err for err in update["errors"])


def test_route_after_perceive(sample_preference):
    """Verifies route_after_perceive directs valid preferences to 'plan' and missing to 'invalid'."""
    valid_state = InternIQAgentState(run_id="r_val", preference=sample_preference)
    assert route_after_perceive(valid_state) == "plan"

    invalid_state = InternIQAgentState(run_id="r_inval", preference=None)
    assert route_after_perceive(invalid_state) == "invalid"


def test_compiled_graph_missing_preference_terminates_immediately(sample_profile):
    """
    Verifies that when preference is missing, the compiled StateGraph immediately
    routes to END without executing plan or act nodes.
    """
    app = build_search_loop_graph()
    result = app.invoke({"run_id": "run_no_pref", "preference": None, "profile": sample_profile})

    assert result["completion_status"] == "failed_validation"
    assert result.get("current_plan") is None
    assert result.get("listings", []) == []
    budget = result.get("budget")
    if budget:
        assert budget.current_replan_count == 0
        assert budget.current_tool_calls == 0


# =====================================================================
# Plan Node Tests
# =====================================================================

def test_plan_node_initial_attempt_1(sample_preference, sample_profile):
    """Verifies that initial planning creates attempt 1 plan and does not increment replan count."""
    state = InternIQAgentState(
        run_id="run_plan_initial",
        preference=sample_preference,
        profile=sample_profile,
    )
    update = plan_node(state)
    assert update["completion_status"] == "planned"
    assert update["iteration"] == 1
    assert update["current_plan"]["attempt"] == 1
    assert update["current_plan"]["relaxed_mode"] is False
    # Budget was not modified
    assert state.budget.current_replan_count == 0


def test_plan_node_replan_increments_budget_once(sample_preference, sample_profile):
    """Verifies that replanning increments the budget counter exactly once per successful replan."""
    planner = SearchPlanner()
    plan_1 = planner.create_plan(sample_preference, attempt=1, profile=sample_profile)
    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results were retrieved."],
        diagnosis="No results retrieved. Re-planning recommended.",
        recommend_replan=True,
    )

    state = InternIQAgentState(
        run_id="run_plan_replan",
        preference=sample_preference,
        profile=sample_profile,
        current_plan=plan_1,
        evaluation=eval_insufficient,
        iteration=1,
    )

    update = plan_node(state)
    assert update["completion_status"] == "replanned"
    assert update["iteration"] == 2
    assert update["current_plan"]["attempt"] == 2
    assert update["current_plan"]["relaxed_mode"] is True
    # Verify budget incremented exactly once in update
    assert update["budget"].current_replan_count == 1
    # Verify input state was not mutated in place
    assert state.budget.current_replan_count == 0


def test_plan_node_with_search_plan_model_instance(sample_preference, sample_profile):
    """
    Verifies plan_node correctly handles SearchPlan model instances (not just dicts),
    extracting the attempt number and producing a valid replan.
    """
    plan_model = SearchPlan(
        plan_id="plan_model_test",
        attempt=1,
        target_sources=["manual_csv"],
        query_keywords=["ml", "python"],
        search_queries=["Machine Learning Intern"],
        filters={"locations": ["Bangalore"], "work_modes": ["remote"], "min_stipend": 30000.0},
        relaxed_mode=False,
        relaxation_notes=[],
    )
    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results."],
        diagnosis="Re-planning recommended.",
        recommend_replan=True,
    )

    state = InternIQAgentState(
        run_id="run_plan_instance",
        preference=sample_preference,
        profile=sample_profile,
        current_plan=plan_model,
        evaluation=eval_insufficient,
        iteration=1,
    )

    update = plan_node(state)
    assert update["completion_status"] == "replanned"
    assert update["iteration"] == 2
    assert update["budget"].current_replan_count == 1


def test_plan_node_budget_exhausted_blocks_replan(sample_preference, sample_profile):
    """Verifies that plan_node does not generate a replan when budget is already exhausted."""
    planner = SearchPlanner()
    plan_4 = planner.create_plan(sample_preference, attempt=4, profile=sample_profile)
    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results."],
        diagnosis="No results.",
        recommend_replan=False,
    )

    budget = ExecutionBudget()
    budget.current_replan_count = 3  # Maximum 3 replans already used

    state = InternIQAgentState(
        run_id="run_plan_capped",
        preference=sample_preference,
        profile=sample_profile,
        budget=budget,
        current_plan=plan_4,
        evaluation=eval_insufficient,
        iteration=4,
    )

    update = plan_node(state)
    assert update["completion_status"] == "budget_exhausted"
    assert "current_plan" not in update


# =====================================================================
# Act Node Tests (Search Execution & Deduplication)
# =====================================================================

def test_act_node_with_manual_csv(sample_preference, sample_profile, sample_csv_content):
    """Verifies act_node parses manual CSV content and records tool execution."""
    planner = SearchPlanner()
    plan_1 = planner.create_plan(sample_preference, attempt=1, profile=sample_profile)

    state = InternIQAgentState(
        run_id="run_act_csv",
        preference=sample_preference,
        profile=sample_profile,
        current_plan=plan_1,
        csv_content=sample_csv_content,
    )

    update = act_node(state)
    assert update["completion_status"] == "searched"
    assert len(update["listings"]) == 3
    assert "manual_csv" in update["source_status"]
    assert "ok: 3 listings" in update["source_status"]["manual_csv"]
    # Tool call counter incremented
    assert update["budget"].current_tool_calls == 1


def test_act_node_with_search_plan_model_instance(sample_preference, sample_profile, sample_csv_content):
    """
    Verifies act_node correctly reads target_sources from a SearchPlan model instance,
    without raising an AttributeError.
    """
    plan_model = SearchPlan(
        plan_id="plan_model_act",
        attempt=1,
        target_sources=["manual_csv"],
        query_keywords=["ml"],
        search_queries=["Machine Learning Intern"],
        filters={},
        relaxed_mode=False,
        relaxation_notes=[],
    )

    state = InternIQAgentState(
        run_id="run_act_instance",
        preference=sample_preference,
        profile=sample_profile,
        current_plan=plan_model,
        csv_content=sample_csv_content,
    )

    update = act_node(state)
    assert update["completion_status"] == "searched"
    assert len(update["listings"]) == 3
    assert "ok: 3 listings" in update["source_status"]["manual_csv"]


def test_act_node_unconfigured_source_safety(sample_preference, sample_profile):
    """Verifies act_node safely flags unconfigured sources without crashing or incrementing tool budget."""
    plan_dict = {
        "target_sources": ["manual_csv", "greenhouse", "lever"],
        "search_queries": ["Machine Learning Intern"],
    }
    state = InternIQAgentState(
        run_id="run_act_unconfigured",
        preference=sample_preference,
        profile=sample_profile,
        current_plan=plan_dict,
        csv_content=None,
        source_config={},  # Missing greenhouse board token and lever site
    )

    update = act_node(state)
    assert update["completion_status"] == "searched"
    assert update["source_status"]["manual_csv"] == "unconfigured: no csv content supplied"
    assert update["source_status"]["greenhouse"] == "unconfigured: missing board token"
    assert update["source_status"]["lever"] == "unconfigured: missing site identifier"
    assert len(update["listings"]) == 0
    # No tool calls were executed for unconfigured sources
    assert update["budget"].current_tool_calls == 0


def test_act_node_cumulative_deduplication(sample_preference, sample_profile, sample_csv_content):
    """Verifies that act_node deduplicates combined previous and newly fetched listings."""
    planner = SearchPlanner()
    plan_1 = planner.create_plan(sample_preference, attempt=1, profile=sample_profile)

    # Initial state already has 3 listings from CSV
    state_1 = InternIQAgentState(
        run_id="run_act_dedupe",
        preference=sample_preference,
        profile=sample_profile,
        current_plan=plan_1,
        csv_content=sample_csv_content,
    )
    update_1 = act_node(state_1)
    assert len(update_1["listings"]) == 3

    # Subsequent round receives the exact same CSV content again
    state_2 = InternIQAgentState(
        run_id="run_act_dedupe",
        preference=sample_preference,
        profile=sample_profile,
        current_plan=plan_1,
        listings=update_1["listings"],
        csv_content=sample_csv_content,
    )
    update_2 = act_node(state_2)
    # Total count must remain 3 due to cumulative deduplication
    assert len(update_2["listings"]) == 3


def test_act_node_source_failure_handled_gracefully(sample_preference, sample_profile):
    """
    Verifies that when a configured fetcher call raises an exception, the tool call is counted,
    the failure is recorded in source_status, and execution does not crash.
    """
    plan_dict = {
        "target_sources": ["greenhouse"],
        "search_queries": ["Machine Learning Intern"],
    }
    state = InternIQAgentState(
        run_id="run_act_failure",
        preference=sample_preference,
        profile=sample_profile,
        current_plan=plan_dict,
        source_config={"greenhouse": "token_that_fails"},
    )

    with patch("agent.graph.fetch_opportunities", side_effect=RuntimeError("Connection timeout")):
        update = act_node(state)

    assert update["completion_status"] == "searched"
    assert "failed: Connection timeout" in update["source_status"]["greenhouse"]
    assert any("Connection timeout" in err for err in update["errors"])
    # Tool call accounting: attempted fetch is counted even when it raises an exception
    assert update["budget"].current_tool_calls == 1


# =====================================================================
# Observe Node Tests
# =====================================================================

def test_observe_node_evaluates_observations(sample_preference, sample_profile, sample_csv_content):
    """Verifies observe_node attaches structured ObservationEvaluation to state."""
    from tools.fetchers import import_csv
    listings = import_csv(sample_csv_content)

    state = InternIQAgentState(
        run_id="run_observe",
        preference=sample_preference,
        profile=sample_profile,
        listings=listings,
        iteration=1,
    )

    update = observe_node(state)
    assert update["completion_status"] == "observed"
    assert update["evaluation"] is not None
    assert update["evaluation"].total_observations == 3
    assert update["evaluation"].is_sufficient is True


# =====================================================================
# Conditional Routing Tests
# =====================================================================

def test_route_after_observe_sufficient():
    """Verifies route_after_observe routes to 'sufficient' when results satisfy criteria."""
    eval_sufficient = ObservationEvaluation(
        is_sufficient=True,
        total_observations=5,
        relevant_observations=4,
        reasons=[],
        diagnosis="Observations are sufficient.",
        recommend_replan=False,
    )
    state = InternIQAgentState(
        run_id="r1",
        evaluation=eval_sufficient,
        iteration=1,
    )
    assert route_after_observe(state) == "sufficient"


def test_route_after_observe_replan():
    """Verifies route_after_observe routes to 'replan' when results are insufficient and budget permits."""
    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results."],
        diagnosis="Re-planning recommended.",
        recommend_replan=True,
    )
    state = InternIQAgentState(
        run_id="r2",
        evaluation=eval_insufficient,
        iteration=1,
    )
    assert route_after_observe(state) == "replan"


def test_route_after_observe_budget_exhausted():
    """Verifies route_after_observe routes to 'exhausted' after attempt 4 or when budget is capped."""
    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results."],
        diagnosis="Budget limit reached.",
        recommend_replan=False,
    )
    # Attempt 4 reached
    state = InternIQAgentState(
        run_id="r3",
        evaluation=eval_insufficient,
        iteration=4,
    )
    assert route_after_observe(state) == "exhausted"


# =====================================================================
# Compiled StateGraph Workflow Tests
# =====================================================================

def test_compiled_graph_sufficient_results_no_replan(sample_preference, sample_profile, sample_csv_content):
    """
    Verifies that a compiled StateGraph run with sufficient matching listings
    terminates at END after attempt 1 with zero replanning rounds used.
    """
    app = build_search_loop_graph()

    initial_input = {
        "run_id": "run_e2e_sufficient",
        "preference": sample_preference,
        "profile": sample_profile,
        "csv_content": sample_csv_content,
    }

    result = app.invoke(initial_input)

    assert result["completion_status"] == "observed"
    assert result["iteration"] == 1
    assert result["budget"].current_replan_count == 0  # 0 replans used
    assert result["evaluation"].is_sufficient is True
    assert len(result["listings"]) == 3


def test_compiled_graph_insufficient_results_exact_three_replans_cap(sample_preference, sample_profile):
    """
    Verifies that a compiled StateGraph run with empty search results
    cycles through exactly 3 replanning rounds (4 total search attempts):
    Attempt 1 (initial) -> Attempt 2 (replan 1) -> Attempt 3 (replan 2) -> Attempt 4 (replan 3) -> END.
    """
    app = build_search_loop_graph()

    # Empty CSV content guarantees zero results on every attempt
    initial_input = {
        "run_id": "run_e2e_insufficient",
        "preference": sample_preference,
        "profile": sample_profile,
        "csv_content": "title,organization,description,locations,work_mode,stipend\n",
    }

    result = app.invoke(initial_input)

    # Must complete after exactly 4 attempts (1 initial + 3 replanning rounds)
    assert result["iteration"] == 4
    assert result["budget"].current_replan_count == 3
    assert result["budget"].can_replan() is False
    assert result["evaluation"].recommend_replan is False
    assert result["current_plan"]["attempt"] == 4
    assert len(result["current_plan"]["relaxation_notes"]) >= 3


def test_compiled_graph_attempt_5_impossible(sample_preference, sample_profile):
    """
    Verifies that Attempt 5 can never be scheduled by the compiled graph:
    Once attempt 4 finishes, route_after_observe strictly directs to END.
    """
    eval_insufficient_4 = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["Zero results."],
        diagnosis="Cap reached.",
        recommend_replan=False,
    )
    budget_capped = ExecutionBudget(current_replan_count=3)

    state = InternIQAgentState(
        run_id="run_att5_check",
        preference=sample_preference,
        profile=sample_profile,
        budget=budget_capped,
        evaluation=eval_insufficient_4,
        iteration=4,
    )

    next_route = route_after_observe(state)
    assert next_route == "exhausted"  # Directs to END, Attempt 5 is blocked
