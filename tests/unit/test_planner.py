"""
Unit Tests for Multi-Source Search Planner & Dynamic Query Formulator
Owner: Samriddhi Raj (Team HumanBot)
Domain: Agent Core & Interface

Tests:
1. Role-based query generation
2. Domain-specialized query combinations
3. Skill-based query generation & low-signal tool filtering
4. Deduplication across roles, domains, and skills
5. Missing or empty candidate profile handling
6. Missing or empty preference fields handling
7. Filter extraction (locations, work modes, stipend, graduation years)
8. Attempt bounds clamping [1, 3] and relaxed_mode flag
9. Backward compatibility with all call signatures (positional & keyword)
10. Type validation and error handling
"""


import pytest

from agent.planner import ObservationEvaluation, SearchPlan, SearchPlanner
from tools.schemas import (
    Education,
    Listing,
    Preference,
    Profile,
    Project,
    WorkMode,
)


@pytest.fixture
def sample_profile() -> Profile:
    return Profile(
        profile_id="prof_planner_test",
        full_name="Samriddhi Raj",
        email="samriddhi@example.edu",
        education=[
            Education(
                degree="B.Tech Computer Science",
                institution="S-VYASA University",
                graduation_year=2026,
                cgpa=9.1,
            )
        ],
        skills=["Python", "PyTorch", "SQL", "Git", "Linux"],
        projects=[
            Project(
                title="Search Planner Agent",
                description="Engineered search query expansion algorithm",
                skills_used=["Python", "PyTorch"],
            )
        ],
    )


@pytest.fixture
def sample_preference() -> Preference:
    return Preference(
        profile_id="prof_planner_test",
        target_roles=["Machine Learning Intern"],
        domains=["FinTech", "Healthcare"],
        locations=["Bangalore", "Remote"],
        work_modes=[WorkMode.REMOTE, WorkMode.HYBRID],
        min_stipend=30000.0,
    )


def test_role_based_query_generation(sample_preference):
    """Verifies that primary target roles are included as exact search queries."""
    planner = SearchPlanner()
    plan = planner.create_plan(sample_preference)

    assert "Machine Learning Intern" in plan["search_queries"]
    assert "Machine Learning Intern" in plan["query_keywords"]
    assert plan["attempt"] == 1
    assert plan["relaxed_mode"] is False


def test_domain_specialized_queries(sample_preference):
    """Verifies domain + role combinations and prevents redundant repetition."""
    planner = SearchPlanner()
    plan = planner.create_plan(sample_preference)

    # Domain-specialized combinations
    assert "FinTech Machine Learning Intern" in plan["search_queries"]
    assert "Healthcare Machine Learning Intern" in plan["search_queries"]

    # Verify no redundant repetition if domain is already in role title
    ai_pref = Preference(
        profile_id="p1",
        target_roles=["AI Engineer Intern"],
        domains=["AI", "Robotics"],
    )
    ai_plan = planner.create_plan(ai_pref)
    assert "AI Engineer Intern" in ai_plan["search_queries"]
    assert "Robotics AI Engineer Intern" in ai_plan["search_queries"]
    # Should NOT contain 'AI AI Engineer Intern'
    assert "AI AI Engineer Intern" not in ai_plan["search_queries"]


def test_skill_based_queries_and_tool_filtering(sample_preference, sample_profile):
    """Verifies skill queries are generated for languages/frameworks but not low-signal tools."""
    planner = SearchPlanner()
    plan = planner.create_plan(sample_preference, profile=sample_profile)

    # High-signal skills generate '<Skill> Intern' queries
    assert "Python Intern" in plan["search_queries"]
    assert "PyTorch Intern" in plan["search_queries"]
    assert "SQL Intern" in plan["search_queries"]

    # Low-signal generic tools (Git, Linux) must NOT generate queries like 'Git Intern'
    assert "Git Intern" not in plan["search_queries"]
    assert "Linux Intern" not in plan["search_queries"]


def test_duplicate_removal():
    """Verifies case-insensitive deduplication across roles, domains, and skills."""
    planner = SearchPlanner()
    pref = Preference(
        profile_id="p1",
        target_roles=["Data Science Intern", "data science intern", "  Data Science Intern  "],
        domains=["AI", "ai", "AI"],
    )
    prof = Profile(
        profile_id="p1",
        full_name="Candidate",
        email="c@example.edu",
        skills=["Python", "python", "PYTHON"],
    )
    plan = planner.create_plan(pref, profile=prof)

    # Count occurrences of 'Data Science Intern' in queries
    ds_queries = [q for q in plan["search_queries"] if q.lower() == "data science intern"]
    assert len(ds_queries) == 1

    # Check keyword uniqueness
    norm_keywords = [k.lower() for k in plan["query_keywords"]]
    assert len(norm_keywords) == len(set(norm_keywords))


def test_missing_or_empty_profile_data(sample_preference):
    """Verifies graceful handling when profile is None or contains empty fields."""
    planner = SearchPlanner()

    # 1. Profile is None
    plan_none = planner.create_plan(sample_preference, profile=None)
    assert plan_none["filters"]["target_graduation_years"] == []
    assert len(plan_none["search_queries"]) > 0

    # 2. Profile has empty skills and empty education
    empty_prof = Profile(
        profile_id="p_empty",
        full_name="Empty Profile",
        email="empty@example.edu",
        education=[],
        skills=[],
    )
    plan_empty = planner.create_plan(sample_preference, profile=empty_prof)
    assert plan_empty["filters"]["target_graduation_years"] == []
    assert "Machine Learning Intern" in plan_empty["search_queries"]


def test_missing_or_empty_preferences():
    """Verifies default role assignment when target_roles is empty or whitespace-only."""
    planner = SearchPlanner()

    # Empty target roles
    pref_empty = Preference(
        profile_id="p1",
        target_roles=[],
        domains=[],
        locations=[],
        work_modes=[],
    )
    plan = planner.create_plan(pref_empty)
    assert plan["search_queries"] == ["Software Engineering Intern"]
    assert plan["filters"]["locations"] == []
    assert plan["filters"]["work_modes"] == []

    # Whitespace-only target roles
    pref_spaces = Preference(
        profile_id="p1",
        target_roles=["   ", ""],
    )
    plan_spaces = planner.create_plan(pref_spaces)
    assert plan_spaces["search_queries"] == ["Software Engineering Intern"]


def test_filter_extraction(sample_preference, sample_profile):
    """Verifies correct population of search filters from preferences and profile."""
    planner = SearchPlanner()
    plan = planner.create_plan(sample_preference, profile=sample_profile)

    filters = plan["filters"]
    assert filters["locations"] == ["Bangalore", "Remote"]
    assert "remote" in filters["work_modes"]
    assert "hybrid" in filters["work_modes"]
    assert filters["min_stipend"] == 30000.0
    assert filters["target_graduation_years"] == [2026]


def test_attempt_boundaries(sample_preference):
    """Verifies attempt clamping [1, 4] and relaxed_mode flag behavior."""
    planner = SearchPlanner()

    # Attempt 1 -> relaxed_mode is False (Work Item 1 generates initial unrelaxed plans)
    plan_1 = planner.create_plan(sample_preference, attempt=1)
    assert plan_1["attempt"] == 1
    assert plan_1["relaxed_mode"] is False

    # Attempt 2 -> relaxed_mode remains False until progressive relaxation
    plan_2 = planner.create_plan(sample_preference, attempt=2)
    assert plan_2["attempt"] == 2
    assert plan_2["relaxed_mode"] is False

    # Attempt 3 -> relaxed_mode remains False until progressive relaxation
    plan_3 = planner.create_plan(sample_preference, attempt=3)
    assert plan_3["attempt"] == 3
    assert plan_3["relaxed_mode"] is False

    # Attempt 4 -> relaxed_mode remains False for initial plans
    plan_4 = planner.create_plan(sample_preference, attempt=4)
    assert plan_4["attempt"] == 4
    assert plan_4["relaxed_mode"] is False

    # Clamping: attempt > 4 is clamped to 4 (1 initial + up to 3 replans)
    plan_high = planner.create_plan(sample_preference, attempt=99)
    assert plan_high["attempt"] == 4

    # Clamping: attempt < 1 is clamped to 1
    plan_low = planner.create_plan(sample_preference, attempt=0)
    assert plan_low["attempt"] == 1
    assert plan_low["relaxed_mode"] is False


def test_backward_compatibility_api(sample_preference, sample_profile):
    """
    Tests backward compatibility across all historical and flexible calling conventions:
    1. planner.create_plan(pref)
    2. planner.create_plan(pref, attempt=2)
    3. planner.create_plan(pref, 2)
    4. planner.create_plan(pref, profile=prof)
    5. planner.create_plan(pref, prof)
    6. planner.create_plan(pref, prof, 2)
    7. planner.create_plan(pref, 2, prof)
    8. planner.create_plan(pref, profile=prof, attempt=2)
    """
    planner = SearchPlanner()

    # 1. create_plan(pref)
    p1 = planner.create_plan(sample_preference)
    assert p1["attempt"] == 1
    assert "query_keywords" in p1
    assert "target_sources" in p1

    # 2. create_plan(pref, attempt=2)
    p2 = planner.create_plan(sample_preference, attempt=2)
    assert p2["attempt"] == 2
    assert p2["relaxed_mode"] is False

    # 3. create_plan(pref, 2) (positional int attempt as in original stub)
    p3 = planner.create_plan(sample_preference, 2)
    assert p3["attempt"] == 2
    assert p3["relaxed_mode"] is False

    # 4. create_plan(pref, profile=prof)
    p4 = planner.create_plan(sample_preference, profile=sample_profile)
    assert p4["attempt"] == 1
    assert "Python Intern" in p4["search_queries"]

    # 5. create_plan(pref, prof) (positional Profile)
    p5 = planner.create_plan(sample_preference, sample_profile)
    assert p5["attempt"] == 1
    assert "Python Intern" in p5["search_queries"]

    # 6. create_plan(pref, prof, 2)
    p6 = planner.create_plan(sample_preference, sample_profile, 2)
    assert p6["attempt"] == 2
    assert "Python Intern" in p6["search_queries"]

    # 7. create_plan(pref, 2, prof)
    p7 = planner.create_plan(sample_preference, 2, sample_profile)
    assert p7["attempt"] == 2
    assert "Python Intern" in p7["search_queries"]

    # 8. create_plan(pref, profile=prof, attempt=2)
    p8 = planner.create_plan(sample_preference, profile=sample_profile, attempt=2)
    assert p8["attempt"] == 2
    assert "Python Intern" in p8["search_queries"]


def test_type_validation_errors(sample_preference):
    """Verifies invalid argument types raise TypeError."""
    planner = SearchPlanner()

    with pytest.raises(TypeError, match="Expected preference to be an instance of Preference schema"):
        planner.create_plan("not_a_preference")

    with pytest.raises(TypeError, match="Expected profile to be an instance of Profile schema or None"):
        planner.create_plan(sample_preference, profile="invalid_profile_type")


# =====================================================================
# Work Item 2: Observation Evaluation & Insufficiency Diagnosis Tests
# =====================================================================

def _create_test_listing(
    listing_id: str,
    title: str,
    description: str = "Internship opportunity description",
    locations: list | None = None,
    work_mode: WorkMode = WorkMode.HYBRID,
    required_skills: list | None = None,
) -> Listing:
    return Listing(
        listing_id=listing_id,
        source_id="greenhouse",
        source_record_id=listing_id,
        canonical_url=f"https://boards.greenhouse.io/job/{listing_id}",
        title=title,
        organization="TestCorp",
        description=description,
        locations=locations if locations is not None else ["Bangalore"],
        work_mode=work_mode,
        required_skills=required_skills or ["Python"],
    )


def test_evaluation_sufficient_results(sample_preference, sample_profile):
    """Verifies that sufficient relevant observations produce is_sufficient=True and no replan recommendation."""
    planner = SearchPlanner()

    listings = [
        _create_test_listing(
            "l1",
            "Machine Learning Intern",
            description="FinTech algorithmic risk and fraud detection models.",
            locations=["Bangalore"],
            work_mode=WorkMode.HYBRID,
        ),
        _create_test_listing(
            "l2",
            "Junior ML Engineer Intern",
            description="Developing financial portfolio AI models.",
            locations=["Bangalore"],
            work_mode=WorkMode.REMOTE,
        ),
        _create_test_listing(
            "l3",
            "AI Research Intern",
            description="Building machine learning pipelines for FinTech banking systems.",
            locations=["Bengaluru, India"],
            work_mode=WorkMode.HYBRID,
        ),
        _create_test_listing(
            "l4",
            "Machine Learning Intern",
            description="Healthcare diagnostics deep learning models.",
            locations=["Bangalore"],
            work_mode=WorkMode.HYBRID,
        ),
    ]

    result = planner.evaluate_observations(
        listings,
        sample_preference,
        sample_profile,
        attempt=1,
        min_sufficient_count=3,
    )

    assert isinstance(result, ObservationEvaluation)
    assert result.is_sufficient is True
    assert result.total_observations == 4
    assert result.relevant_observations == 4
    assert result.total_listings == 4
    assert result.relevant_listings == 4
    assert result.recommend_replan is False
    assert result.should_replan is False
    assert len(result.reasons) == 0
    assert len(result.relevant_listing_ids) == 4
    assert "sufficient" in result.diagnosis.lower()


def test_evaluation_no_search_results(sample_preference):
    """Verifies handling of completely empty observation sets (0 results)."""
    planner = SearchPlanner()

    result = planner.evaluate_observations([], sample_preference, attempt=1)

    assert result.is_sufficient is False
    assert result.total_observations == 0
    assert result.relevant_observations == 0
    assert result.recommend_replan is True
    assert any("No search results were retrieved" in r for r in result.reasons)
    assert "no listings found" in result.diagnosis.lower()


def test_evaluation_too_few_results(sample_preference):
    """Verifies insufficiency diagnosis when results are below the required threshold."""
    planner = SearchPlanner()

    listings = [
        _create_test_listing(
            "l1",
            "Machine Learning Intern",
            description="FinTech risk modeling.",
            locations=["Bangalore"],
            work_mode=WorkMode.HYBRID,
        )
    ]

    result = planner.evaluate_observations(
        listings,
        sample_preference,
        attempt=1,
        min_sufficient_count=3,
    )

    assert result.is_sufficient is False
    assert result.total_observations == 1
    assert result.relevant_observations == 1
    assert result.recommend_replan is True
    assert any("Too few relevant listings found" in r for r in result.reasons)
    assert "insufficient" in result.diagnosis.lower()


def test_evaluation_role_mismatches(sample_preference):
    """Verifies that listings matching domains/locations but failing role match are caught."""
    planner = SearchPlanner()

    listings = [
        _create_test_listing(
            "l1",
            "Sales Representative Intern",
            description="FinTech client outreach and lead generation.",
            locations=["Bangalore"],
            work_mode=WorkMode.HYBRID,
        ),
        _create_test_listing(
            "l2",
            "Finance & Accounting Intern",
            description="FinTech quarterly reconciliation.",
            locations=["Bangalore"],
            work_mode=WorkMode.REMOTE,
        ),
        _create_test_listing(
            "l3",
            "Human Resources Intern",
            description="FinTech talent acquisition.",
            locations=["Bangalore"],
            work_mode=WorkMode.HYBRID,
        ),
    ]

    result = planner.evaluate_observations(listings, sample_preference, attempt=1)

    assert result.is_sufficient is False
    assert result.role_match_count == 0
    assert result.relevant_observations == 0
    assert result.recommend_replan is True
    assert any("No listings matched target roles" in r for r in result.reasons)


def test_evaluation_domain_mismatches():
    """Verifies that listings with matching roles but non-matching domains are diagnosed."""
    planner = SearchPlanner()
    pref = Preference(
        profile_id="p_domain",
        target_roles=["Backend Intern"],
        domains=["FinTech"],
        locations=["Bangalore"],
        work_modes=[WorkMode.HYBRID],
    )

    listings = [
        _create_test_listing(
            "l1",
            "Backend Intern",
            description="Building gaming physics engines and 3D rendering pipelines.",
            locations=["Bangalore"],
            work_mode=WorkMode.HYBRID,
        ),
        _create_test_listing(
            "l2",
            "Backend Intern",
            description="Developing agricultural soil monitoring IoT pipelines.",
            locations=["Bangalore"],
            work_mode=WorkMode.HYBRID,
        ),
        _create_test_listing(
            "l3",
            "Backend Intern",
            description="Music streaming playlist compression algorithms.",
            locations=["Bangalore"],
            work_mode=WorkMode.HYBRID,
        ),
    ]

    result = planner.evaluate_observations(listings, pref, attempt=1)

    assert result.is_sufficient is False
    assert result.domain_match_count == 0
    assert result.relevant_observations == 0
    assert any("No listings matched preferred domains" in r for r in result.reasons)
    assert result.recommend_replan is True


def test_evaluation_location_mismatches():
    """Verifies that listings in non-preferred locations or conflicting work modes are diagnosed."""
    planner = SearchPlanner()
    pref = Preference(
        profile_id="p_loc",
        target_roles=["Backend Intern"],
        locations=["Bangalore"],
        work_modes=[WorkMode.ON_SITE],
    )

    listings = [
        _create_test_listing(
            "l1",
            "Backend Intern",
            description="Backend microservices.",
            locations=["London, UK"],
            work_mode=WorkMode.ON_SITE,
        ),
        _create_test_listing(
            "l2",
            "Backend Intern",
            description="API development.",
            locations=["Berlin, Germany"],
            work_mode=WorkMode.ON_SITE,
        ),
        _create_test_listing(
            "l3",
            "Backend Intern",
            description="Distributed databases.",
            locations=["New York, USA"],
            work_mode=WorkMode.ON_SITE,
        ),
    ]

    result = planner.evaluate_observations(listings, pref, attempt=1)

    assert result.is_sufficient is False
    assert result.location_match_count == 0
    assert result.relevant_observations == 0
    assert any("No listings matched preferred locations" in r for r in result.reasons)
    assert result.recommend_replan is True


def test_evaluation_missing_or_incomplete_listing_fields(sample_preference):
    """
    Verifies that malformed listings or listings with missing required fields
    are not silently treated as successful matches.
    """
    planner = SearchPlanner()

    raw_items = [
        # Missing title
        {"listing_id": "malformed_1", "title": "", "locations": ["Bangalore"]},
        # Missing listing ID
        {"listing_id": "", "title": "Machine Learning Intern", "locations": ["Bangalore"]},
        # Incomplete schema: missing locations when user requires physical locations
        _create_test_listing(
            "incomplete_loc",
            "Machine Learning Intern",
            description="FinTech risk modeling.",
            locations=[],
            work_mode=WorkMode.UNKNOWN,
        ),
        # Non-dict, non-Listing invalid object
        12345,
    ]

    result = planner.evaluate_observations(raw_items, sample_preference, attempt=1)

    assert result.is_sufficient is False
    assert result.unusable_count == 3
    assert result.relevant_observations == 0
    assert any("unusable or malformed" in r for r in result.reasons)


def test_evaluation_diagnosis_and_replanning_recommendation_bounds(sample_preference):
    """
    Verifies that replanning is recommended on attempts 1, 2, and 3,
    but stops after attempt 4 per ExecutionBudget limits (3 replan rounds).
    """
    planner = SearchPlanner()

    # Attempt 1 -> recommend_replan is True
    res_att1 = planner.evaluate_observations([], sample_preference, attempt=1)
    assert res_att1.recommend_replan is True

    # Attempt 2 -> recommend_replan is True
    res_att2 = planner.evaluate_observations([], sample_preference, attempt=2)
    assert res_att2.recommend_replan is True

    # Attempt 3 -> recommend_replan is True (final replan to produce attempt 4)
    res_att3 = planner.evaluate_observations([], sample_preference, attempt=3)
    assert res_att3.recommend_replan is True

    # Attempt 4 -> recommend_replan is False (budget limit reached: 3/3 replans completed)
    res_att4 = planner.evaluate_observations([], sample_preference, attempt=4)
    assert res_att4.recommend_replan is False
    assert "budget limit reached" in res_att4.diagnosis or "budget cap reached" in res_att4.diagnosis

    # Singular evaluate_observation alias
    single_res = planner.evaluate_observation(None, sample_preference, attempt=1)
    assert single_res.is_sufficient is False
    assert single_res.total_observations == 0


def test_evaluation_with_dict_and_model_listings(sample_preference):
    """Verifies that dict inputs, Listing models, and property aliases work interchangeably."""
    planner = SearchPlanner()

    mixed_items = [
        _create_test_listing(
            "l_model",
            "Machine Learning Intern",
            description="FinTech credit risk models.",
            locations=["Bangalore"],
            work_mode=WorkMode.REMOTE,
        ),
        {
            "listing_id": "l_dict",
            "title": "Machine Learning Intern",
            "description": "FinTech fraud detection algorithms.",
            "locations": ["Bangalore, India"],
            "work_mode": "remote",
        },
    ]

    result = planner.evaluate_observations(mixed_items, sample_preference, min_sufficient_count=2)

    assert result.is_sufficient is True
    assert result.total_observations == 2
    assert result.relevant_observations == 2
    assert result.total_listings == 2
    assert result.relevant_listings == 2
    assert result.should_replan is False
    assert set(result.relevant_listing_ids) == {"l_model", "l_dict"}


def test_evaluation_type_validation_errors(sample_preference):
    """Verifies that invalid argument types in evaluate_observations raise TypeError."""
    planner = SearchPlanner()

    with pytest.raises(TypeError, match="Expected preference to be an instance of Preference schema"):
        planner.evaluate_observations([], "invalid_preference")

    with pytest.raises(TypeError, match="Expected profile to be an instance of Profile schema or None"):
        planner.evaluate_observations([], sample_preference, profile="invalid_profile")


# =====================================================================
# Work Item 3: Progressive Constraint Relaxation Tests
# =====================================================================

def test_replan_four_attempt_progression(sample_preference, sample_profile):
    """
    Verifies the four-attempt lifecycle:
    Attempt 1 (initial) -> Attempt 2 (replan 1) -> Attempt 3 (replan 2) -> Attempt 4 (replan 3) -> None (attempt 5 blocked).
    """
    planner = SearchPlanner()
    pref_with_stipend_opt_in = Preference(
        profile_id="p_prog",
        target_roles=["Machine Learning Intern"],
        domains=["FinTech"],
        locations=["Bangalore"],
        work_modes=[WorkMode.REMOTE, WorkMode.HYBRID],
        min_stipend=30000.0,
        constraints={"flexible_stipend": True},
    )

    # 1. Attempt 1: Initial search plan
    plan_1 = planner.create_plan(pref_with_stipend_opt_in, profile=sample_profile, attempt=1)
    assert plan_1["attempt"] == 1
    assert plan_1["relaxed_mode"] is False

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results retrieved."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    # 2. Attempt 1 -> Attempt 2 (Replan 1: Location relaxation)
    plan_2 = planner.replan(plan_1, eval_insufficient, pref_with_stipend_opt_in, sample_profile)
    assert plan_2 is not None
    assert plan_2["attempt"] == 2
    assert plan_2["relaxed_mode"] is True
    assert plan_2["filters"]["locations"] == []

    # 3. Attempt 2 -> Attempt 3 (Replan 2: Domain relaxation)
    plan_3 = planner.replan(plan_2, eval_insufficient, pref_with_stipend_opt_in, sample_profile)
    assert plan_3 is not None
    assert plan_3["attempt"] == 3
    assert plan_3["relaxed_mode"] is True
    assert any("domain" in n.lower() for n in plan_3["relaxation_notes"])

    # 4. Attempt 3 -> Attempt 4 (Replan 3: Stipend relaxation & query broadening)
    plan_4 = planner.replan(plan_3, eval_insufficient, pref_with_stipend_opt_in, sample_profile)
    assert plan_4 is not None
    assert plan_4["attempt"] == 4
    assert plan_4["relaxed_mode"] is True
    assert plan_4["filters"]["min_stipend"] is None

    # 5. Attempt 4 -> None (Execution budget cap reached: 3 replans max, Attempt 5 blocked)
    plan_5 = planner.replan(plan_4, eval_insufficient, pref_with_stipend_opt_in, sample_profile)
    assert plan_5 is None


def test_replan_stipend_preserved_by_default(sample_preference, sample_profile):
    """
    Correction 2: Verifies that min_stipend is NEVER relaxed by default when constraints={}.
    An explicit user minimum stipend must not be silently weakened.
    """
    planner = SearchPlanner()
    pref_default = Preference(
        profile_id="p_default",
        target_roles=["Machine Learning Intern"],
        domains=["FinTech"],
        locations=["Bangalore"],
        work_modes=[WorkMode.REMOTE],
        min_stipend=35000.0,
        constraints={},  # Default empty constraints
    )

    plan_1 = planner.create_plan(pref_default, profile=sample_profile, attempt=1)
    assert plan_1["filters"]["min_stipend"] == 35000.0

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results retrieved."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    # Attempt 2
    plan_2 = planner.replan(plan_1, eval_insufficient, pref_default, sample_profile)
    assert plan_2 is not None
    assert plan_2["filters"]["min_stipend"] == 35000.0

    # Attempt 3
    plan_3 = planner.replan(plan_2, eval_insufficient, pref_default, sample_profile)
    assert plan_3 is not None
    assert plan_3["filters"]["min_stipend"] == 35000.0

    # Attempt 4
    plan_4 = planner.replan(plan_3, eval_insufficient, pref_default, sample_profile)
    assert plan_4 is not None
    assert plan_4["filters"]["min_stipend"] == 35000.0


def test_replan_stipend_preserved_when_opt_in_false(sample_preference, sample_profile):
    """
    Correction 2: Verifies that min_stipend remains preserved when flexible_stipend=False
    or allow_unpaid=False.
    """
    planner = SearchPlanner()
    pref_explicit_false = Preference(
        profile_id="p_false",
        target_roles=["Machine Learning Intern"],
        domains=["FinTech"],
        locations=["Bangalore"],
        work_modes=[WorkMode.REMOTE],
        min_stipend=40000.0,
        constraints={"flexible_stipend": False, "allow_unpaid": False},
    )

    plan_1 = planner.create_plan(pref_explicit_false, profile=sample_profile, attempt=1)
    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    plan_2 = planner.replan(plan_1, eval_insufficient, pref_explicit_false, sample_profile)
    plan_3 = planner.replan(plan_2, eval_insufficient, pref_explicit_false, sample_profile)
    plan_4 = planner.replan(plan_3, eval_insufficient, pref_explicit_false, sample_profile)

    assert plan_4 is not None
    assert plan_4["filters"]["min_stipend"] == 40000.0


def test_replan_stipend_relaxed_when_flexible_stipend_true(sample_preference, sample_profile):
    """
    Correction 2: Verifies stipend relaxation occurs when flexible_stipend=True.
    """
    planner = SearchPlanner()
    pref_flexible = Preference(
        profile_id="p_flex",
        target_roles=["Machine Learning Intern"],
        domains=["FinTech"],
        locations=["Bangalore"],
        work_modes=[WorkMode.REMOTE],
        min_stipend=30000.0,
        constraints={"flexible_stipend": True},
    )

    plan_1 = planner.create_plan(pref_flexible, profile=sample_profile, attempt=1)
    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    plan_2 = planner.replan(plan_1, eval_insufficient, pref_flexible, sample_profile)
    plan_3 = planner.replan(plan_2, eval_insufficient, pref_flexible, sample_profile)

    # Stipend is relaxed on attempt 3 or 4 when permitted
    plan_4 = planner.replan(plan_3, eval_insufficient, pref_flexible, sample_profile)
    assert plan_4 is not None
    assert plan_4["filters"]["min_stipend"] is None
    assert any("flexible stipend" in n for n in plan_4["relaxation_notes"])


def test_replan_stipend_relaxed_when_allow_unpaid_true(sample_preference, sample_profile):
    """
    Correction 2: Verifies stipend relaxation occurs when allow_unpaid=True.
    """
    planner = SearchPlanner()
    pref_unpaid = Preference(
        profile_id="p_unpaid",
        target_roles=["Backend Intern"],
        domains=[],
        locations=[],
        work_modes=[WorkMode.REMOTE],
        min_stipend=25000.0,
        constraints={"allow_unpaid": True},
    )

    plan_1 = planner.create_plan(pref_unpaid, profile=sample_profile, attempt=1)
    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    plan_2 = planner.replan(plan_1, eval_insufficient, pref_unpaid, sample_profile)
    assert plan_2 is not None
    assert plan_2["filters"]["min_stipend"] is None
    assert any("flexible stipend" in n for n in plan_2["relaxation_notes"])


def test_replan_location_relaxation_attempt_2(sample_preference, sample_profile):
    """Verifies that attempt 2 relaxes strict location filters when results are insufficient."""
    planner = SearchPlanner()
    plan_1 = planner.create_plan(sample_preference, profile=sample_profile, attempt=1)

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=1,
        relevant_observations=1,
        reasons=["Too few relevant listings found (1 found, minimum required is 3)."],
        diagnosis="Observations are insufficient. Re-planning recommended.",
        recommend_replan=True,
    )

    plan_2 = planner.replan(plan_1, eval_insufficient, sample_preference, sample_profile)

    assert plan_2 is not None
    assert plan_2["attempt"] == 2
    assert plan_2["relaxed_mode"] is True
    # Strict city locations filter relaxed from ['Bangalore', 'Remote'] to []
    assert plan_2["filters"]["locations"] == []
    # Work modes remain strictly preserved
    assert plan_2["filters"]["work_modes"] == ["remote", "hybrid"]
    assert any("Relaxed strict location filter" in note for note in plan_2["relaxation_notes"])


def test_replan_domain_relaxation_attempt_3(sample_preference, sample_profile):
    """Verifies that attempt 3 relaxes domain constraints while preserving target roles."""
    planner = SearchPlanner()
    plan_1 = planner.create_plan(sample_preference, profile=sample_profile, attempt=1)

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results were retrieved."],
        diagnosis="No results retrieved. Re-planning recommended.",
        recommend_replan=True,
    )

    plan_2 = planner.replan(plan_1, eval_insufficient, sample_preference, sample_profile)
    assert plan_2 is not None
    assert plan_2["attempt"] == 2

    # Now evaluate attempt 2 as still insufficient
    eval_insufficient_2 = ObservationEvaluation(
        is_sufficient=False,
        total_observations=1,
        relevant_observations=0,
        reasons=["No listings matched preferred domains: FinTech, Healthcare."],
        diagnosis="Domain mismatch diagnosed. Re-planning recommended.",
        recommend_replan=True,
    )

    plan_3 = planner.replan(plan_2, eval_insufficient_2, sample_preference, sample_profile)

    assert plan_3 is not None
    assert plan_3["attempt"] == 3
    assert plan_3["relaxed_mode"] is True
    assert any("Relaxed domain constraint" in note for note in plan_3["relaxation_notes"])
    # Default stipend is preserved since flexible_stipend is not opted in
    assert plan_3["filters"]["min_stipend"] == sample_preference.min_stipend
    # Target roles preserved in search queries
    assert any("Machine Learning Intern" in q for q in plan_3["search_queries"])


def test_replan_no_relaxation_when_results_sufficient(sample_preference, sample_profile):
    """Verifies that no new plan is generated when observations are already sufficient."""
    planner = SearchPlanner()
    plan_1 = planner.create_plan(sample_preference, profile=sample_profile, attempt=1)

    eval_sufficient = ObservationEvaluation(
        is_sufficient=True,
        total_observations=5,
        relevant_observations=4,
        reasons=[],
        diagnosis="Observations are sufficient: 4 of 5 listings match user preferences.",
        recommend_replan=False,
    )

    result = planner.replan(plan_1, eval_sufficient, sample_preference, sample_profile)
    assert result is None


def test_replan_no_relaxation_after_maximum_attempt(sample_preference, sample_profile):
    """Verifies that no plan is generated beyond attempt 4 per Section 10.6 execution bounds."""
    planner = SearchPlanner()
    plan_4 = planner.create_plan(sample_preference, profile=sample_profile, attempt=4)

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results were retrieved."],
        diagnosis="Execution budget cap reached (4/4 attempts, 3/3 replan rounds completed).",
        recommend_replan=False,
    )

    result = planner.replan(plan_4, eval_insufficient, sample_preference, sample_profile)
    assert result is None


def test_replan_preserves_target_roles(sample_preference, sample_profile):
    """Verifies that target roles are strictly preserved across all progressive relaxation rounds."""
    planner = SearchPlanner()
    plan_1 = planner.create_plan(sample_preference, profile=sample_profile, attempt=1)

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results were retrieved."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    plan_2 = planner.replan(plan_1, eval_insufficient, sample_preference, sample_profile)
    assert plan_2 is not None
    assert "Machine Learning Intern" in plan_2["search_queries"]
    assert sample_preference.target_roles == ["Machine Learning Intern"]

    plan_3 = planner.replan(plan_2, eval_insufficient, sample_preference, sample_profile)
    assert plan_3 is not None
    assert any("Machine Learning Intern" in q for q in plan_3["search_queries"])
    assert sample_preference.target_roles == ["Machine Learning Intern"]

    plan_4 = planner.replan(plan_3, eval_insufficient, sample_preference, sample_profile)
    assert plan_4 is not None
    assert any("Machine Learning Intern" in q for q in plan_4["search_queries"])
    assert sample_preference.target_roles == ["Machine Learning Intern"]


def test_replan_no_mutation_of_inputs(sample_preference, sample_profile):
    """Verifies that replan never mutates the original plan, preference, or profile objects."""
    planner = SearchPlanner()
    plan_1 = planner.create_plan(sample_preference, profile=sample_profile, attempt=1)

    import copy
    plan_1_copy = copy.deepcopy(plan_1)
    pref_copy = sample_preference.model_dump()
    prof_copy = sample_profile.model_dump()

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results were retrieved."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    plan_2 = planner.replan(plan_1, eval_insufficient, sample_preference, sample_profile)

    assert plan_2 is not None
    assert plan_1 == plan_1_copy
    assert sample_preference.model_dump() == pref_copy
    assert sample_profile.model_dump() == prof_copy


def test_replan_relaxation_notes_accuracy(sample_preference, sample_profile):
    """Verifies that relaxation notes accurately describe each progressive adjustment."""
    planner = SearchPlanner()
    pref_with_opt_in = Preference(
        profile_id="p_opt",
        target_roles=["Machine Learning Intern"],
        domains=["FinTech"],
        locations=["Bangalore"],
        work_modes=[WorkMode.REMOTE],
        min_stipend=30000.0,
        constraints={"flexible_stipend": True},
    )

    plan_1 = planner.create_plan(pref_with_opt_in, profile=sample_profile, attempt=1)
    assert plan_1["relaxation_notes"] == []

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results were retrieved."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    plan_2 = planner.replan(plan_1, eval_insufficient, pref_with_opt_in, sample_profile)
    assert len(plan_2["relaxation_notes"]) == 1
    assert "Attempt 2: Relaxed strict location filter" in plan_2["relaxation_notes"][0]

    plan_3 = planner.replan(plan_2, eval_insufficient, pref_with_opt_in, sample_profile)
    assert len(plan_3["relaxation_notes"]) >= 2
    assert any("Attempt 3: Relaxed domain constraint" in n for n in plan_3["relaxation_notes"])

    plan_4 = planner.replan(plan_3, eval_insufficient, pref_with_opt_in, sample_profile)
    assert len(plan_4["relaxation_notes"]) >= 3
    assert any("Attempt 4: Relaxed minimum stipend filter" in n for n in plan_4["relaxation_notes"])


def test_replan_relaxed_mode_flag_behavior(sample_preference, sample_profile):
    """Verifies that relaxed_mode is False on initial plans and True only after actual relaxation."""
    planner = SearchPlanner()
    plan_1 = planner.create_plan(sample_preference, profile=sample_profile, attempt=1)
    assert plan_1["relaxed_mode"] is False

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results were retrieved."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    plan_2 = planner.replan(plan_1, eval_insufficient, sample_preference, sample_profile)
    assert plan_2["relaxed_mode"] is True


def test_replan_no_duplicate_plans_when_relaxation_impossible():
    """Verifies that no plan is generated if all candidate constraints are explicitly hard."""
    planner = SearchPlanner()
    strict_pref = Preference(
        profile_id="p_strict",
        target_roles=["Backend Intern"],
        domains=["FinTech"],
        locations=["Bangalore"],
        work_modes=[WorkMode.HYBRID],
        min_stipend=50000.0,
        constraints={
            "hard_location": True,
            "hard_domain": True,
            "hard_stipend": True,
        },
    )

    plan_1 = planner.create_plan(strict_pref, attempt=1)

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results were retrieved."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    result = planner.replan(plan_1, eval_insufficient, strict_pref)
    assert result is None


def test_replan_with_search_plan_instance_and_alias(sample_preference, sample_profile):
    """Verifies that replan accepts SearchPlan model instances and supports relax_plan alias."""
    planner = SearchPlanner()
    plan_dict = planner.create_plan(sample_preference, profile=sample_profile, attempt=1)
    plan_model = SearchPlan(**plan_dict)

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results were retrieved."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    plan_2 = planner.relax_plan(plan_model, eval_insufficient, sample_preference, sample_profile)
    assert plan_2 is not None
    assert plan_2["attempt"] == 2
    assert plan_2["relaxed_mode"] is True


def test_replan_type_validation_errors(sample_preference):
    """Verifies that invalid argument types raise TypeError."""
    planner = SearchPlanner()
    plan_1 = planner.create_plan(sample_preference, attempt=1)

    eval_insufficient = ObservationEvaluation(
        is_sufficient=False,
        total_observations=0,
        relevant_observations=0,
        reasons=["No search results."],
        diagnosis="No results.",
        recommend_replan=True,
    )

    with pytest.raises(TypeError, match="Expected evaluation to be an instance of ObservationEvaluation"):
        planner.replan(plan_1, "not_an_evaluation", sample_preference)

    with pytest.raises(TypeError, match="Expected preference to be an instance of Preference"):
        planner.replan(plan_1, eval_insufficient, "not_a_preference")

    with pytest.raises(TypeError, match="Expected profile to be an instance of Profile or None"):
        planner.replan(plan_1, eval_insufficient, sample_preference, profile="invalid_profile")

    with pytest.raises(TypeError, match="Expected current_plan to be SearchPlan or dict"):
        planner.replan(12345, eval_insufficient, sample_preference)
