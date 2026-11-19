"""
Multi-Source Search Planner and Query Formulator
Owner: Samriddhi Raj (Team HumanBot)
Domain: Agent Core & Interface

Implements Section 5.2 (Node 2: Plan), Section 7.1, and FR-03:
- Generates dynamic, role-tailored internship search queries from candidate Profile and Preference.
- Determines multi-source ingestion targets (Greenhouse, Lever, CSV).
- Constructs structured search plans with location, work mode, and stipend constraints.
"""

import uuid
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from tools.schemas import Preference, Profile, WorkMode

# Tools that are valuable on a resume but poor as standalone job search titles
# e.g., "Python Intern" is a great query, but "Git Intern" or "Linux Intern" is not.
GENERIC_TOOL_SKILLS = {
    "git", "github", "linux", "bash", "shell", "jira", "ci/cd",
    "rest api", "html", "css", "docker", "kubernetes"
}


class SearchPlan(BaseModel):
    """
    Structured search plan consumed by data ingestion fetchers and agent loop.
    """
    plan_id: str = Field(..., description="Unique search plan identifier")
    attempt: int = Field(1, ge=1, le=3, description="Plan iteration attempt (1 to 3)")
    target_sources: List[str] = Field(
        default_factory=lambda: ["greenhouse", "lever", "manual_csv"],
        description="Target job boards and authorized connectors",
    )
    query_keywords: List[str] = Field(
        default_factory=list,
        description="Atomic keywords (roles, skills, domains)",
    )
    search_queries: List[str] = Field(
        default_factory=list,
        description="Constructed query strings formatted for search APIs",
    )
    filters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Search constraints (locations, work modes, stipend, graduation years)",
    )
    relaxed_mode: bool = Field(
        False,
        description="Whether constraints were relaxed for fallback discovery",
    )
    relaxation_notes: List[str] = Field(
        default_factory=list,
        description="Audit trail of constraint relaxation steps across attempts",
    )


class SearchPlanner:
    """
    Formulates dynamic multi-source search plans and search query variations
    grounded in the student's confirmed profile and target preferences.
    """

    def __init__(self, default_sources: Optional[List[str]] = None):
        self.default_sources: List[str] = (
            list(default_sources)
            if default_sources is not None
            else ["greenhouse", "lever", "manual_csv"]
        )

    def _extract_keywords(
        self,
        preference: Preference,
        profile: Optional[Profile] = None,
    ) -> List[str]:
        """
        Extracts high-signal deduplicated keywords from target roles, domains, and top profile skills.
        Safely handles empty, None, or whitespace-only inputs.
        """
        keywords: List[str] = []
        seen = set()

        def add_keyword(k: str):
            clean = k.strip()
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                keywords.append(clean)

        # 1. Primary target roles
        raw_roles = [r for r in (preference.target_roles or []) if r and r.strip()]
        roles = raw_roles or ["Software Engineering Intern"]
        for role in roles:
            add_keyword(role)

        # 2. Preferred domains
        for domain in (preference.domains or []):
            if domain and domain.strip():
                add_keyword(domain)

        # 3. Top skills from candidate profile (if provided)
        if profile and profile.skills:
            for skill in profile.skills[:5]:
                if skill and skill.strip():
                    add_keyword(skill)

        return keywords

    def _generate_queries(
        self,
        roles: List[str],
        domains: List[str],
        skills: List[str],
    ) -> List[str]:
        """
        Combines roles, domains, and technical skills into specific, non-redundant search queries.
        Filters out low-signal standalone tool queries (e.g., 'Git Intern').
        """
        queries: List[str] = []
        seen = set()

        def add_query(q: str):
            clean = " ".join(q.split()).strip()
            if clean and clean.lower() not in seen:
                seen.add(clean.lower())
                queries.append(clean)

        # 1. Primary queries: Exact target roles
        for role in roles:
            add_query(role)

        # 2. Domain-specialized queries
        for domain in domains:
            for role in roles:
                # Avoid redundant repetitions like 'AI AI Engineer Intern'
                if domain.lower() not in role.lower():
                    add_query(f"{domain} {role}")
                else:
                    add_query(role)

        # 3. Meaningful skill-focused queries
        meaningful_skills = [
            s for s in skills
            if s and s.strip() and s.strip().lower() not in GENERIC_TOOL_SKILLS
        ]
        for skill in meaningful_skills[:3]:
            # If skill is already part of a target role (e.g. 'Python' in 'Python Developer Intern'), skip
            role_combined_text = " ".join(roles).lower()
            if skill.lower() not in role_combined_text:
                add_query(f"{skill} Intern")

        return queries

    def create_plan(
        self,
        preference: Preference,
        attempt: Union[int, Profile] = 1,
        profile: Optional[Profile] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Creates a bounded, structured search plan with dynamic query formulation.

        Backward Compatibility:
        - create_plan(preference)                      -> attempt=1, profile=None
        - create_plan(preference, attempt=2)           -> attempt=2, profile=None
        - create_plan(preference, 2)                   -> attempt=2, profile=None
        - create_plan(preference, profile=profile)     -> attempt=1, profile=profile
        - create_plan(preference, profile)             -> attempt=1, profile=profile
        - create_plan(preference, profile, 2)          -> attempt=2, profile=profile
        - create_plan(preference, 2, profile)          -> attempt=2, profile=profile
        """
        if not isinstance(preference, Preference):
            raise TypeError(
                f"Expected preference to be an instance of Preference schema, got {type(preference).__name__}"
            )

        # Handle positional parameter flexibility
        if isinstance(attempt, Profile):
            # Caller passed profile as 2nd positional argument: create_plan(pref, prof, ...)
            actual_profile = attempt
            actual_attempt = profile if isinstance(profile, int) else kwargs.get("attempt", 1)
            profile = actual_profile
            attempt = actual_attempt
        elif profile is not None and not isinstance(profile, Profile):
            raise TypeError(
                f"Expected profile to be an instance of Profile schema or None, got {type(profile).__name__}"
            )

        # Safe attempt clamping to bounds [1, 3] per Section 10.6 execution limits
        try:
            int_attempt = int(attempt)
        except (ValueError, TypeError):
            int_attempt = 1
        clamped_attempt = max(1, min(int_attempt, 3))

        # Safe extraction of roles, domains, and skills
        raw_roles = [r.strip() for r in (preference.target_roles or []) if r and r.strip()]
        roles = raw_roles or ["Software Engineering Intern"]
        domains = [d.strip() for d in (preference.domains or []) if d and d.strip()]
        skills = [s.strip() for s in (profile.skills or []) if s and s.strip()] if profile else []

        keywords = self._extract_keywords(preference, profile)
        queries = self._generate_queries(roles, domains, skills)

        # Extract filters from student preference and candidate profile
        work_modes = [
            wm.value if hasattr(wm, "value") else str(wm)
            for wm in (preference.work_modes or [])
        ]
        locations = [loc.strip() for loc in (preference.locations or []) if loc and loc.strip()]

        grad_years: List[int] = []
        if profile and profile.education:
            for edu in profile.education:
                gy = getattr(edu, "graduation_year", None)
                if gy is not None and isinstance(gy, int):
                    grad_years.append(gy)

        filters: Dict[str, Any] = {
            "locations": locations,
            "work_modes": work_modes,
            "min_stipend": preference.min_stipend,
            "target_graduation_years": grad_years,
        }

        plan_id = f"plan_{uuid.uuid4().hex[:8]}_att{clamped_attempt}"
        plan = SearchPlan(
            plan_id=plan_id,
            attempt=clamped_attempt,
            target_sources=list(self.default_sources),
            query_keywords=keywords,
            search_queries=queries,
            filters=filters,
            relaxed_mode=False,
            relaxation_notes=[],
        )

        return plan.model_dump()
