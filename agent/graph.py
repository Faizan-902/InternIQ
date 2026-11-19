"""
LangGraph Agent Workflow and State Machine
Owner: Samriddhi Raj (Team HumanBot)
Status: Milestone 3 Work Item 1 (Search-Observe-Replan Loop)

This module coordinates the InternIQ agent workflow.
Milestone 3 Work Item 1 implements the Search-Observe-Replan discovery loop:
START -> Perceive -> Plan -> Act -> Observe -> Conditional Route -> (Replan -> Plan | Sufficient -> END | Exhausted -> END)

Downstream pipeline stages (Vet, Rank, Generate, Approve, Record) will be integrated
in Milestone 3 Work Items 2-4.
"""

from datetime import datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from agent.limits import ExecutionBudget
from agent.planner import ObservationEvaluation, SearchPlan, SearchPlanner
from tools.fetchers import fetch_opportunities, normalize_dedupe
from tools.schemas import (
    ApplicationDraft,
    Approval,
    EligibilityResult,
    Listing,
    Preference,
    Profile,
    RiskFlag,
    ScoreBreakdown,
)


def _get_plan_field(plan: Any, field_name: str, default: Any = None) -> Any:
    """
    Safely extracts a field from a SearchPlan model instance, raw dict, or duck-typed object.
    """
    if plan is None:
        return default
    if isinstance(plan, dict):
        return plan.get(field_name, default)
    return getattr(plan, field_name, default)


class InternIQAgentState(BaseModel):
    run_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    profile: Profile | None = None
    preference: Preference | None = None
    budget: ExecutionBudget = Field(default_factory=ExecutionBudget)

    # Discovery & Listings
    listings: list[Listing] = Field(default_factory=list)
    source_status: dict[str, str] = Field(default_factory=dict)

    # Work Item 1: Search Plan & Observation Evaluation tracking
    current_plan: SearchPlan | dict[str, Any] | None = None
    evaluation: ObservationEvaluation | None = None
    iteration: int = 1
    csv_content: str | None = None
    source_config: dict[str, Any] = Field(default_factory=dict)

    # Vetting & Ranking (Mohd Faizan Khan's components)
    eligibility_map: dict[str, EligibilityResult] = Field(default_factory=dict)
    risk_map: dict[str, list[RiskFlag]] = Field(default_factory=dict)
    scores_map: dict[str, ScoreBreakdown] = Field(default_factory=dict)
    ranked_listing_ids: list[str] = Field(default_factory=list)

    # Generation & Approval
    drafts_map: dict[str, ApplicationDraft] = Field(default_factory=dict)
    approval: Approval | None = None
    errors: list[str] = Field(default_factory=list)
    completion_status: str = "in_progress"


# =====================================================================
# Milestone 3, Work Item 1: Node Definitions
# =====================================================================

def perceive_node(state: InternIQAgentState) -> dict[str, Any]:
    """
    Validates and prepares candidate preference and profile.
    Returns state updates without mutating the input state object.
    """
    errors = list(state.errors or [])
    if state.preference is None:
        errors.append("Validation error: Candidate preference is missing.")
        return {
            "completion_status": "failed_validation",
            "errors": errors,
        }

    return {
        "completion_status": "perceived",
        "errors": errors,
    }


def route_after_perceive(state: InternIQAgentState) -> str:
    """
    Conditional routing function evaluated after perceive_node:
    - Returns 'plan' if candidate preference is present and valid.
    - Returns 'invalid' (routing to END) if candidate preference is missing.
    """
    if state.preference is None:
        return "invalid"
    return "plan"


def plan_node(state: InternIQAgentState) -> dict[str, Any]:
    """
    Formulates the initial search plan (Attempt 1) or generates a progressively
    broadened search plan (Attempts 2, 3, 4) using SearchPlanner.
    Increments ExecutionBudget.record_replan() exactly once per successful replan.
    Accepts both SearchPlan model instances and dictionary representations.
    """
    planner = SearchPlanner()
    preference = state.preference
    profile = state.profile
    budget = state.budget

    if state.current_plan is None:
        # Initial search plan generation (0 replans used)
        plan = planner.create_plan(preference, attempt=1, profile=profile)
        return {
            "current_plan": plan,
            "iteration": 1,
            "completion_status": "planned",
        }
    else:
        # Re-planning round (Attempts 2, 3, 4)
        if not budget.can_replan():
            return {
                "completion_status": "budget_exhausted",
            }

        evaluation = state.evaluation
        if evaluation is None:
            # Cannot replan without prior evaluation
            return {
                "completion_status": "missing_evaluation",
            }

        new_plan = planner.replan(
            current_plan=state.current_plan,
            evaluation=evaluation,
            preference=preference,
            profile=profile,
        )

        if new_plan is not None:
            new_budget = budget.model_copy(deep=True)
            new_budget.record_replan()  # Exactly once per successful replan
            attempt_num = _get_plan_field(new_plan, "attempt", state.iteration + 1)
            return {
                "current_plan": new_plan,
                "iteration": attempt_num,
                "budget": new_budget,
                "completion_status": "replanned",
            }
        else:
            return {
                "completion_status": "replan_exhausted",
            }


def act_node(state: InternIQAgentState) -> dict[str, Any]:
    """
    Executes search queries against authorized connectors.
    Safely handles unconfigured or unavailable sources.
    Performs cumulative deduplication of existing and newly fetched listings.
    Counts attempted tool calls consistently, including when fetch raises an exception.
    """
    current_plan = state.current_plan
    target_sources = _get_plan_field(current_plan, "target_sources", ["manual_csv"])
    source_status = dict(state.source_status or {})
    errors = list(state.errors or [])
    new_budget = state.budget.model_copy(deep=True)

    newly_fetched: list[Listing] = []

    # Check for manual CSV content in state or preference constraints
    csv_content = state.csv_content
    if not csv_content and state.preference and state.preference.constraints:
        csv_content = state.preference.constraints.get("csv_content")

    for source in target_sources:
        s_lower = str(source).lower().strip()
        try:
            if s_lower in ("manual_csv", "csv"):
                if csv_content:
                    new_budget.record_tool_call()
                    fetched = fetch_opportunities(source_id="manual_csv", csv_content=csv_content)
                    newly_fetched.extend(fetched)
                    source_status[source] = f"ok: {len(fetched)} listings"
                else:
                    source_status[source] = "unconfigured: no csv content supplied"

            elif s_lower == "greenhouse":
                token = state.source_config.get("greenhouse")
                if not token and state.preference and state.preference.constraints:
                    token = state.preference.constraints.get("greenhouse_tokens")
                if token:
                    tokens = [token] if isinstance(token, str) else list(token)
                    count_for_source = 0
                    for tok in tokens:
                        new_budget.record_tool_call()
                        fetched = fetch_opportunities(source_id="greenhouse", target_identifier=tok)
                        newly_fetched.extend(fetched)
                        count_for_source += len(fetched)
                    source_status[source] = f"ok: {count_for_source} listings"
                else:
                    source_status[source] = "unconfigured: missing board token"

            elif s_lower == "lever":
                site = state.source_config.get("lever")
                if not site and state.preference and state.preference.constraints:
                    site = state.preference.constraints.get("lever_sites")
                if site:
                    sites = [site] if isinstance(site, str) else list(site)
                    count_for_source = 0
                    for s in sites:
                        new_budget.record_tool_call()
                        fetched = fetch_opportunities(source_id="lever", target_identifier=s)
                        newly_fetched.extend(fetched)
                        count_for_source += len(fetched)
                    source_status[source] = f"ok: {count_for_source} listings"
                else:
                    source_status[source] = "unconfigured: missing site identifier"

            else:
                source_status[source] = f"unsupported: source '{source}' not recognized"

        except Exception as exc:
            source_status[source] = f"failed: {exc!s}"
            errors.append(f"Source '{source}' error: {exc!s}")

    # Cumulative deduplication: combine previous listings with newly fetched listings
    combined = list(state.listings or []) + newly_fetched
    deduped_listings = normalize_dedupe(combined)

    return {
        "listings": deduped_listings,
        "source_status": source_status,
        "budget": new_budget,
        "errors": errors,
        "completion_status": "searched",
    }


def observe_node(state: InternIQAgentState) -> dict[str, Any]:
    """
    Evaluates retrieved listings against target candidate preferences.
    Produces structured ObservationEvaluation diagnosing sufficiency root causes.
    """
    planner = SearchPlanner()
    attempt = state.iteration
    evaluation = planner.evaluate_observations(
        observations=state.listings,
        preference=state.preference,
        profile=state.profile,
        attempt=attempt,
    )
    return {
        "evaluation": evaluation,
        "completion_status": "observed",
    }


def route_after_observe(state: InternIQAgentState) -> str:
    """
    Conditional routing function evaluated after observe_node:
    - Returns 'sufficient' if listings satisfy sufficiency thresholds -> END.
    - Returns 'replan' if results are insufficient, replan recommended, and budget allows -> plan_node.
    - Returns 'exhausted' if replanning budget is exhausted (Attempt 4 reached) -> END.
    """
    evaluation = state.evaluation
    if evaluation is None:
        return "exhausted"

    if evaluation.is_sufficient:
        return "sufficient"

    budget = state.budget
    iteration = state.iteration

    # Re-planning is permitted only if evaluation recommends it,
    # budget has rounds remaining, and attempt is less than 4 (Attempt 4 is the final search)
    if evaluation.recommend_replan and budget.can_replan() and iteration < 4:
        return "replan"

    return "exhausted"


def build_search_loop_graph() -> Any:
    """
    Compiles the Milestone 3 Work Item 1 StateGraph workflow:
    START -> perceive -> (valid: plan | invalid: END) -> act -> observe -> route_after_observe -> (replan -> plan | sufficient -> END | exhausted -> END)
    """
    workflow = StateGraph(InternIQAgentState)

    workflow.add_node("perceive", perceive_node)
    workflow.add_node("plan", plan_node)
    workflow.add_node("act", act_node)
    workflow.add_node("observe", observe_node)

    workflow.add_edge(START, "perceive")
    workflow.add_conditional_edges(
        "perceive",
        route_after_perceive,
        {
            "plan": "plan",
            "invalid": END,
        },
    )
    workflow.add_edge("plan", "act")
    workflow.add_edge("act", "observe")

    workflow.add_conditional_edges(
        "observe",
        route_after_observe,
        {
            "replan": "plan",
            "sufficient": END,
            "exhausted": END,
        },
    )

    return workflow.compile()
