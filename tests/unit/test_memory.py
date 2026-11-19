"""
Unit Tests for Three-Layer Agent Memory Architecture
Owner: Samriddhi Raj (Team HumanBot)
Domain: Agent Core & Interface

Tests:
1. Profile Memory: set, get, load, persist, and type validation
2. Episodic Memory: feedback recording, past application retrieval, episode recording
3. Run Working State: scratchpad update, get, clear
4. Privacy & DPDP: purge_all_data wipes RAM and SQLite persistence
5. Database Integration: dependency injection and custom db_path
"""

import pytest
from datetime import datetime
from agent.memory import AgentMemoryManager
from tools.tracker import ApplicationTracker
from tools.schemas import (
    Profile,
    Education,
    Project,
    EvidenceFact,
    Preference,
    WorkMode,
    TrackerEvent,
    TrackerEventType,
)


@pytest.fixture
def sample_profile() -> Profile:
    return Profile(
        profile_id="prof_test_samriddhi",
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
        skills=["Python", "LangGraph", "Streamlit"],
        projects=[
            Project(
                title="InternIQ Agent",
                description="Engineered agent orchestration and memory architecture",
                skills_used=["Python", "LangGraph"],
            )
        ],
        confirmed_facts=[
            EvidenceFact(
                fact_id="fact_sam_1",
                fact_type="skill",
                text="Proficient in Python and LangGraph orchestration",
                source_doc="resume.pdf",
                confidence=1.0,
                confirmed_by_user=True,
            )
        ],
    )


@pytest.fixture
def sample_preference() -> Preference:
    return Preference(
        profile_id="prof_test_samriddhi",
        target_roles=["AI Engineer Intern", "Software Engineering Intern"],
        domains=["AI", "Backend"],
        locations=["Bangalore", "Remote"],
        work_modes=[WorkMode.REMOTE, WorkMode.HYBRID],
        min_stipend=25000.0,
    )


def test_memory_initialization_defaults_and_injection(tmp_path):
    """Verifies that AgentMemoryManager accepts custom db_path or injected tracker."""
    db_file = str(tmp_path / "custom_test.db")
    custom_tracker = ApplicationTracker(db_path=db_file)

    # Injected tracker
    mem = AgentMemoryManager(tracker=custom_tracker)
    assert mem.tracker is custom_tracker
    assert mem.profile_memory is None
    assert mem.preference_memory is None
    assert mem.episodic_history == []
    assert mem.run_scratchpad == {}

    # Initialized via db_path
    mem_path = AgentMemoryManager(db_path=db_file)
    assert mem_path.tracker.db_path == db_file


def test_profile_memory_set_get_and_persistence(tmp_path, sample_profile):
    """Tests set_profile stores in RAM and writes to SQLite."""
    db_file = str(tmp_path / "profile_test.db")
    mem = AgentMemoryManager(db_path=db_file)

    # 1. Set and persist
    mem.set_profile(sample_profile, persist=True)
    assert mem.get_profile() is sample_profile
    assert mem.get_profile().profile_id == "prof_test_samriddhi"

    # Verify SQLite has it
    loaded_from_db = mem.tracker.get_profile("prof_test_samriddhi")
    assert loaded_from_db is not None
    assert loaded_from_db.full_name == "Samriddhi Raj"
    assert loaded_from_db.skills == ["Python", "LangGraph", "Streamlit"]
    assert len(loaded_from_db.confirmed_facts) == 1

    # 2. Set without persistence
    temp_profile = sample_profile.model_copy(update={"profile_id": "prof_temp"})
    mem.set_profile(temp_profile, persist=False)
    assert mem.get_profile().profile_id == "prof_temp"
    assert mem.tracker.get_profile("prof_temp") is None


def test_profile_memory_load_from_sqlite(tmp_path, sample_profile):
    """Tests load_profile rehydrates in-memory cache from database."""
    db_file = str(tmp_path / "load_test.db")
    tracker = ApplicationTracker(db_path=db_file)
    tracker.save_profile(sample_profile)

    # New memory manager with empty RAM
    mem = AgentMemoryManager(tracker=tracker)
    assert mem.get_profile() is None

    # Load from DB
    reloaded = mem.load_profile("prof_test_samriddhi")
    assert reloaded is not None
    assert reloaded.profile_id == "prof_test_samriddhi"
    assert mem.get_profile() is reloaded

    # Load non-existent profile returns None
    assert mem.load_profile("non_existent_id") is None
    assert mem.load_profile("") is None


def test_profile_memory_type_validation(tmp_path):
    """Ensures invalid types for profile or preference raise TypeError."""
    db_file = str(tmp_path / "types_test.db")
    mem = AgentMemoryManager(db_path=db_file)

    with pytest.raises(TypeError, match="Expected instance of Profile schema"):
        mem.set_profile({"profile_id": "invalid"})  # dict instead of Profile

    with pytest.raises(TypeError, match="Expected instance of Preference schema"):
        mem.set_preference("Software Engineer")  # string instead of Preference


def test_preference_memory_set_and_get(tmp_path, sample_preference):
    """Tests set_preference and get_preference in working memory."""
    db_file = str(tmp_path / "pref_test.db")
    mem = AgentMemoryManager(db_path=db_file)

    assert mem.get_preference() is None
    mem.set_preference(sample_preference)
    assert mem.get_preference() is sample_preference
    assert mem.get_preference().target_roles == ["AI Engineer Intern", "Software Engineering Intern"]


def test_episodic_memory_record_feedback(tmp_path):
    """Tests record_feedback saves to SQLite and records episodic history."""
    db_file = str(tmp_path / "feedback_test.db")
    mem = AgentMemoryManager(db_path=db_file)

    fb_id = mem.record_feedback(
        opportunity_id="opp_100",
        label="good_match",
        reason="Aligned with LangGraph skills",
    )
    assert fb_id.startswith("fb_")
    assert len(mem.episodic_history) == 1
    assert mem.episodic_history[0]["feedback_id"] == fb_id
    assert mem.episodic_history[0]["label"] == "good_match"

    # Validation errors on empty parameters
    with pytest.raises(ValueError, match="opportunity_id and label are required"):
        mem.record_feedback(opportunity_id="", label="good_match")

    with pytest.raises(ValueError, match="opportunity_id and label are required"):
        mem.record_feedback(opportunity_id="opp_100", label="")


def test_episodic_memory_get_past_application_ids(tmp_path):
    """Tests retrieval of already-applied opportunity IDs from tracker events."""
    db_file = str(tmp_path / "applied_test.db")
    mem = AgentMemoryManager(db_path=db_file)

    # Initially empty
    assert mem.get_past_application_ids() == []

    # Record two applied events and one shortlisted event
    mem.tracker.record_event(
        TrackerEvent(
            event_id="evt_app_1",
            opportunity_id="opp_google",
            event_type=TrackerEventType.APPLIED,
            event_time=datetime.utcnow(),
            user_confirmed=True,
            idempotency_key="idemp_app_1",
        )
    )
    mem.tracker.record_event(
        TrackerEvent(
            event_id="evt_short_1",
            opportunity_id="opp_meta",
            event_type=TrackerEventType.SHORTLISTED,
            event_time=datetime.utcnow(),
            user_confirmed=True,
            idempotency_key="idemp_short_1",
        )
    )
    mem.tracker.record_event(
        TrackerEvent(
            event_id="evt_app_2",
            opportunity_id="opp_amazon",
            event_type=TrackerEventType.APPLIED,
            event_time=datetime.utcnow(),
            user_confirmed=True,
            idempotency_key="idemp_app_2",
        )
    )

    applied_ids = mem.get_past_application_ids()
    assert "opp_google" in applied_ids
    assert "opp_amazon" in applied_ids
    assert "opp_meta" not in applied_ids
    assert len(applied_ids) == 2


def test_episodic_memory_record_episode(tmp_path):
    """Tests backward-compatible record_episode method."""
    db_file = str(tmp_path / "episode_test.db")
    mem = AgentMemoryManager(db_path=db_file)

    episode = {"stage": "search", "round": 1, "found_count": 5}
    mem.record_episode(episode)
    assert len(mem.episodic_history) == 1
    assert mem.episodic_history[0] == episode

    with pytest.raises(TypeError, match="Expected episode to be a dict"):
        mem.record_episode("string_episode")


def test_run_working_state_scratchpad(tmp_path):
    """Tests run working state scratchpad set, get, default, and clear."""
    db_file = str(tmp_path / "scratch_test.db")
    mem = AgentMemoryManager(db_path=db_file)

    assert mem.get_run_state("plan_attempt") is None
    assert mem.get_run_state("plan_attempt", default=1) == 1

    mem.update_run_state("plan_attempt", 2)
    mem.update_run_state("discovered_ids", ["id_1", "id_2"])
    assert mem.get_run_state("plan_attempt") == 2
    assert mem.get_run_state("discovered_ids") == ["id_1", "id_2"]

    # Clear run state
    mem.clear_run_state()
    assert mem.run_scratchpad == {}
    assert mem.get_run_state("plan_attempt") is None

    # Error handling
    with pytest.raises(ValueError, match="Scratchpad key must be a non-empty string"):
        mem.update_run_state("", "value")


def test_privacy_purge_all_data(tmp_path, sample_profile, sample_preference):
    """Tests purge_all_data completely clears both in-memory and SQLite storage."""
    db_file = str(tmp_path / "purge_test.db")
    mem = AgentMemoryManager(db_path=db_file)

    # Populate RAM and SQLite
    mem.set_profile(sample_profile, persist=True)
    mem.set_preference(sample_preference)
    mem.update_run_state("active_job", "job_123")
    mem.record_feedback("opp_1", "good_match")
    mem.tracker.record_event(
        TrackerEvent(
            event_id="evt_purge_1",
            opportunity_id="opp_1",
            event_type=TrackerEventType.SAVED,
            event_time=datetime.utcnow(),
            user_confirmed=True,
            idempotency_key="idemp_purge_1",
        )
    )

    assert mem.get_profile() is not None
    assert mem.get_preference() is not None
    assert len(mem.episodic_history) > 0
    assert len(mem.run_scratchpad) > 0
    assert len(mem.tracker.get_events()) == 1

    # Execute full purge
    deleted_events = mem.purge_all_data()
    assert deleted_events == 1

    # In-memory assertions
    assert mem.get_profile() is None
    assert mem.get_preference() is None
    assert mem.episodic_history == []
    assert mem.run_scratchpad == {}

    # SQLite persistence assertions
    assert mem.tracker.get_profile("prof_test_samriddhi") is None
    assert len(mem.tracker.get_events()) == 0
