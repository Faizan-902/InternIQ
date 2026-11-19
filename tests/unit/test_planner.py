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
from agent.planner import SearchPlanner, SearchPlan
from tools.schemas import (
    Preference,
    Profile,
    Education,
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
    """Verifies attempt clamping [1, 3] and relaxed_mode flag behavior."""
    planner = SearchPlanner()

    # Attempt 1 -> relaxed_mode is False (Work Item 1 generates initial unrelaxed plans)
    plan_1 = planner.create_plan(sample_preference, attempt=1)
    assert plan_1["attempt"] == 1
    assert plan_1["relaxed_mode"] is False

    # Attempt 2 -> relaxed_mode remains False until Work Item 3 progressive relaxation
    plan_2 = planner.create_plan(sample_preference, attempt=2)
    assert plan_2["attempt"] == 2
    assert plan_2["relaxed_mode"] is False

    # Attempt 3 -> relaxed_mode remains False until Work Item 3 progressive relaxation
    plan_3 = planner.create_plan(sample_preference, attempt=3)
    assert plan_3["attempt"] == 3
    assert plan_3["relaxed_mode"] is False

    # Clamping: attempt > 3 is clamped to 3
    plan_high = planner.create_plan(sample_preference, attempt=99)
    assert plan_high["attempt"] == 3

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
