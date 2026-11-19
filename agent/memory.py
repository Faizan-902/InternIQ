"""
Three-Layer Agent Memory Architecture
Owner: Samriddhi Raj (Team HumanBot)
Domain: Agent Core & Interface

Implements Section 7.1 & Table 6 of InternIQ Technical Report:
1. Profile Memory: Parsed education, skills, projects, and atomic verified facts with provenance.
2. Episodic Memory: Search constraints, saved/applied roles, and student ranking feedback.
3. Run Working State: Short-lived intermediate plan, observations, and scratchpad variables.
"""

from typing import Any

from tools.schemas import Preference, Profile, TrackerEventType
from tools.tracker import ApplicationTracker


class AgentMemoryManager:
    """
    Coordinates the 3-layer memory hierarchy:
    - Layer 1 (Profile Memory): In-memory profile with persistent SQLite backing.
    - Layer 2 (Episodic Memory): Search constraints, feedback, and past application history.
    - Layer 3 (Run Working State): Ephemeral scratchpad for active LangGraph execution runs.
    """

    def __init__(
        self,
        db_path: str = "interniq.db",
        tracker: ApplicationTracker | None = None,
    ):
        """
        Initializes the memory manager with dependency-injected ApplicationTracker.
        """
        self.tracker: ApplicationTracker = (
            tracker if tracker is not None else ApplicationTracker(db_path=db_path)
        )
        self.profile_memory: Profile | None = None
        self.preference_memory: Preference | None = None
        self.episodic_history: list[dict[str, Any]] = []
        self.run_scratchpad: dict[str, Any] = {}

    # =========================================================================
    # Layer 1: Profile Memory
    # =========================================================================

    def load_profile(self, profile_id: str) -> Profile | None:
        """
        Loads a candidate profile from the SQLite persistent database by profile_id.
        Updates the in-memory cache and returns the reconstructed Profile.
        """
        if not profile_id or not isinstance(profile_id, str):
            return None
        profile = self.tracker.get_profile(profile_id)
        if profile is not None:
            self.profile_memory = profile
        return profile

    def set_profile(self, profile: Profile, persist: bool = True) -> None:
        """
        Sets the in-memory profile and optionally persists it to the SQLite database.
        Preserves backward compatibility with existing set_profile(profile) calls.
        """
        if not isinstance(profile, Profile):
            raise TypeError(f"Expected instance of Profile schema, got {type(profile).__name__}")
        self.profile_memory = profile
        if persist and self.tracker is not None:
            self.tracker.save_profile(profile)

    def get_profile(self) -> Profile | None:
        """Returns the currently active candidate profile from memory."""
        return self.profile_memory

    def set_preference(self, preference: Preference) -> None:
        """Sets the currently active candidate preference in memory."""
        if not isinstance(preference, Preference):
            raise TypeError(
                f"Expected instance of Preference schema, got {type(preference).__name__}"
            )
        self.preference_memory = preference

    def get_preference(self) -> Preference | None:
        """Returns the currently active candidate preference from memory."""
        return self.preference_memory

    # =========================================================================
    # Layer 2: Episodic Memory
    # =========================================================================

    def record_feedback(
        self, opportunity_id: str, label: str, reason: str | None = None
    ) -> str:
        """
        Stores candidate ranking feedback (e.g., 'good_match', 'irrelevant')
        in SQLite persistence and appends it to episodic history.
        """
        if not opportunity_id or not label:
            raise ValueError("opportunity_id and label are required to record feedback.")

        feedback_id = self.tracker.save_feedback(
            opportunity_id=opportunity_id, label=label, reason=reason
        )
        self.episodic_history.append(
            {
                "type": "feedback",
                "feedback_id": feedback_id,
                "opportunity_id": opportunity_id,
                "label": label,
                "reason": reason,
            }
        )
        return feedback_id

    def get_past_application_ids(self) -> list[str]:
        """
        Retrieves the unique opportunity IDs for which the student has recorded
        an 'applied' lifecycle event, preventing redundant recommendations.
        """
        events = self.tracker.get_events()
        applied_ids: list[str] = []
        seen = set()

        for ev in events:
            etype = ev.get("event_type")
            if etype in (
                TrackerEventType.APPLIED.value,
                TrackerEventType.APPLIED,
                "applied",
            ):
                opp_id = ev.get("opportunity_id")
                if opp_id and opp_id not in seen:
                    seen.add(opp_id)
                    applied_ids.append(opp_id)

        return applied_ids

    def record_episode(self, episode: dict[str, Any]) -> None:
        """
        Records an episodic execution event or intermediate search summary.
        Preserves backward compatibility with the existing skeleton.
        """
        if not isinstance(episode, dict):
            raise TypeError(f"Expected episode to be a dict, got {type(episode).__name__}")
        self.episodic_history.append(episode)

    # =========================================================================
    # Layer 3: Run Working State (Scratchpad)
    # =========================================================================

    def update_run_state(self, key: str, value: Any) -> None:
        """Sets a key-value pair in the active run scratchpad."""
        if not key or not isinstance(key, str):
            raise ValueError("Scratchpad key must be a non-empty string.")
        self.run_scratchpad[key] = value

    def get_run_state(self, key: str, default: Any = None) -> Any:
        """Retrieves a key from the active run scratchpad."""
        return self.run_scratchpad.get(key, default)

    def clear_run_state(self) -> None:
        """
        Clears all temporary variables from the run scratchpad.
        Preserves backward compatibility with the existing skeleton.
        """
        self.run_scratchpad.clear()

    # =========================================================================
    # Privacy & DPDP Compliance
    # =========================================================================

    def purge_all_data(self) -> int:
        """
        Full DPDP / GDPR local data erasure.
        Wipes all in-memory profile, preference, episodic, and scratchpad data,
        and delegates to ApplicationTracker.delete_all_user_data() to wipe SQLite.
        Returns the count of erased tracker events.
        """
        self.profile_memory = None
        self.preference_memory = None
        self.episodic_history.clear()
        self.run_scratchpad.clear()
        return self.tracker.delete_all_user_data()
