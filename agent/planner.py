"""
Multi-Source Search Planner and Query Formulator
Owner: Samriddhi Raj (Team HumanBot)
Domain: Agent Core & Interface

Implements Section 5.2 (Node 2: Plan), Section 7.1, and FR-03:
- Generates dynamic, role-tailored internship search queries from candidate Profile and Preference.
- Determines multi-source ingestion targets (Greenhouse, Lever, CSV).
- Constructs structured search plans with location, work mode, and stipend constraints.
"""

import copy
import re
import uuid
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from tools.schemas import Preference, Profile, WorkMode, Listing

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
    attempt: int = Field(1, ge=1, le=4, description="Plan iteration attempt (1 to 4: 1 initial + up to 3 replans)")
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


class ObservationEvaluation(BaseModel):
    """
    Structured outcome of evaluating retrieved internship opportunities (observations)
    against candidate target preferences and profile constraints.
    """
    is_sufficient: bool = Field(
        ...,
        description="Whether the observations meet sufficiency criteria for downstream workflow",
    )
    total_observations: int = Field(
        ...,
        ge=0,
        description="Total raw count of listings observed during this search iteration",
    )
    relevant_observations: int = Field(
        ...,
        ge=0,
        description="Number of observations satisfying role, domain, location, and work mode criteria",
    )
    reasons: List[str] = Field(
        default_factory=list,
        description="Itemized explanations for insufficiency or limitations",
    )
    diagnosis: str = Field(
        ...,
        description="Synthesized natural-language diagnostic summary of observation quality",
    )
    recommend_replan: bool = Field(
        ...,
        description="Whether another search iteration or query re-planning is recommended",
    )
    relevant_listing_ids: List[str] = Field(
        default_factory=list,
        description="Listing IDs of opportunities meeting relevance criteria",
    )
    unusable_count: int = Field(
        0,
        ge=0,
        description="Count of malformed or unusable listings missing required fields (e.g. title or ID)",
    )
    role_match_count: int = Field(
        0,
        ge=0,
        description="Count of listings matching target role criteria",
    )
    domain_match_count: int = Field(
        0,
        ge=0,
        description="Count of listings matching preferred domain criteria",
    )
    location_match_count: int = Field(
        0,
        ge=0,
        description="Count of listings matching preferred locations or work modes",
    )

    @property
    def total_listings(self) -> int:
        """Alias for total_observations."""
        return self.total_observations

    @property
    def relevant_listings(self) -> int:
        """Alias for relevant_observations."""
        return self.relevant_observations

    @property
    def should_replan(self) -> bool:
        """Alias for recommend_replan."""
        return self.recommend_replan


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

        # Safe attempt clamping to bounds [1, 4] (1 initial + up to 3 replans) per Section 10.6
        try:
            int_attempt = int(attempt)
        except (ValueError, TypeError):
            int_attempt = 1
        clamped_attempt = max(1, min(int_attempt, 4))

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

    def _normalize_listing(self, item: Any) -> Optional[Dict[str, Any]]:
        """
        Safely extracts and validates fields from a Listing model instance, raw dict, or duck-typed object.
        Returns None if essential fields (title, listing_id) are missing or blank.
        """
        if item is None:
            return None

        if isinstance(item, Listing):
            listing_id = str(item.listing_id or "").strip()
            title = str(item.title or "").strip()
            if not listing_id or not title:
                return None
            return {
                "listing_id": listing_id,
                "title": title,
                "description": str(item.description or "").strip(),
                "locations": [str(loc).strip() for loc in (item.locations or []) if str(loc).strip()],
                "work_mode": item.work_mode,
                "required_skills": [str(s).strip() for s in (item.required_skills or []) if str(s).strip()],
                "preferred_skills": [str(s).strip() for s in (item.preferred_skills or []) if str(s).strip()],
            }

        if isinstance(item, dict):
            listing_id = str(item.get("listing_id") or item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not listing_id or not title:
                return None
            desc = str(item.get("description") or "").strip()

            raw_locs = item.get("locations") or item.get("location") or []
            if isinstance(raw_locs, str):
                locs = [l.strip() for l in raw_locs.split(",") if l.strip()]
            elif isinstance(raw_locs, (list, tuple)):
                locs = [str(l).strip() for l in raw_locs if str(l).strip()]
            else:
                locs = []

            raw_wm = item.get("work_mode", WorkMode.UNKNOWN)
            if isinstance(raw_wm, WorkMode):
                work_mode = raw_wm
            else:
                wm_str = str(raw_wm).lower().replace("-", "_").strip()
                try:
                    work_mode = WorkMode(wm_str)
                except ValueError:
                    work_mode = WorkMode.UNKNOWN

            raw_req = item.get("required_skills") or []
            if isinstance(raw_req, str):
                req_skills = [s.strip() for s in raw_req.split(",") if s.strip()]
            elif isinstance(raw_req, (list, tuple)):
                req_skills = [str(s).strip() for s in raw_req if str(s).strip()]
            else:
                req_skills = []

            raw_pref = item.get("preferred_skills") or []
            if isinstance(raw_pref, str):
                pref_skills = [s.strip() for s in raw_pref.split(",") if s.strip()]
            elif isinstance(raw_pref, (list, tuple)):
                pref_skills = [str(s).strip() for s in raw_pref if str(s).strip()]
            else:
                pref_skills = []

            return {
                "listing_id": listing_id,
                "title": title,
                "description": desc,
                "locations": locs,
                "work_mode": work_mode,
                "required_skills": req_skills,
                "preferred_skills": pref_skills,
            }

        if hasattr(item, "title") and hasattr(item, "listing_id"):
            listing_id = str(getattr(item, "listing_id") or "").strip()
            title = str(getattr(item, "title") or "").strip()
            if not listing_id or not title:
                return None
            return {
                "listing_id": listing_id,
                "title": title,
                "description": str(getattr(item, "description", "") or "").strip(),
                "locations": [str(l).strip() for l in getattr(item, "locations", []) or [] if str(l).strip()],
                "work_mode": getattr(item, "work_mode", WorkMode.UNKNOWN),
                "required_skills": [str(s).strip() for s in getattr(item, "required_skills", []) or [] if str(s).strip()],
                "preferred_skills": [str(s).strip() for s in getattr(item, "preferred_skills", []) or [] if str(s).strip()],
            }

        return None

    def _matches_role(self, title: str, description: str, target_roles: List[str]) -> bool:
        """
        Evaluates whether a listing matches any of the target roles.
        If no target roles are specified, all usable roles match.
        """
        valid_roles = [r.strip() for r in (target_roles or []) if r and r.strip()]
        if not valid_roles:
            return True

        title_lower = title.lower()
        generic_tokens = {"intern", "internship", "trainee", "junior", "associate", "fresher", "co-op", "coop"}

        for role in valid_roles:
            role_lower = role.lower()
            if role_lower in title_lower:
                return True

            role_words = [
                w for w in re.findall(r"\b[a-zA-Z0-9+#.-]+\b", role_lower)
                if w not in generic_tokens
            ]

            if role_words:
                if all(w in title_lower for w in role_words):
                    return True

                if ("machine" in role_words and "learning" in role_words) and ("ml" in title_lower or "ai" in title_lower):
                    return True
                if ("artificial" in role_words and "intelligence" in role_words) and ("ai" in title_lower or "ml" in title_lower):
                    return True
                if ("front" in role_words and "end" in role_words) and ("frontend" in title_lower or "ui" in title_lower):
                    return True
                if ("back" in role_words and "end" in role_words) and ("backend" in title_lower):
                    return True
            else:
                if any(w in title_lower for w in generic_tokens):
                    return True

        return False

    def _matches_domain(
        self,
        title: str,
        description: str,
        skills: List[str],
        domains: List[str],
    ) -> bool:
        """
        Evaluates whether a listing matches any preferred domain.
        Since Listing does not have a native 'domain' attribute, the planner
        inspects title, description text, and skills for domain signals.
        """
        valid_domains = [d.strip() for d in (domains or []) if d and d.strip()]
        if not valid_domains:
            return True

        corpus = f"{title} {description[:1500]} {' '.join(skills)}".lower()

        for domain in valid_domains:
            d_lower = domain.lower()
            if not d_lower:
                continue

            if d_lower in {"ai", "ml", "ui", "ux", "ar", "vr"}:
                if re.search(rf"\b{re.escape(d_lower)}\b", corpus):
                    return True
                if d_lower == "ai" and ("artificial intelligence" in corpus or "machine learning" in corpus):
                    return True
                if d_lower == "ml" and ("machine learning" in corpus or "deep learning" in corpus):
                    return True
            elif d_lower in {"fintech", "fin-tech"}:
                if any(w in corpus for w in ["fintech", "finance", "financial", "banking", "bank", "payments", "trading", "crypto", "wealth"]):
                    return True
            elif d_lower in {"edtech", "ed-tech"}:
                if any(w in corpus for w in ["edtech", "education", "educational", "learning", "curriculum", "ed-tech", "teaching", "tutor"]):
                    return True
            elif d_lower in {"healthtech", "health-tech", "medtech", "healthcare"}:
                if any(w in corpus for w in ["healthtech", "healthcare", "health", "medical", "clinical", "biotech", "pharma"]):
                    return True
            elif d_lower in {"backend", "back-end"}:
                if any(w in corpus for w in ["backend", "back-end", "server", "api", "database"]):
                    return True
            elif d_lower in {"cloud", "cloud computing"}:
                if any(w in corpus for w in ["cloud", "aws", "azure", "gcp", "devops"]):
                    return True
            elif d_lower in {"cybersecurity", "security"}:
                if any(w in corpus for w in ["cybersecurity", "security", "infosec", "penetration", "vulnerability"]):
                    return True
            else:
                if d_lower in corpus:
                    return True

        return False

    def _matches_location(
        self,
        listing_locations: List[str],
        listing_work_mode: Any,
        pref_locations: List[str],
        pref_work_modes: List[Any],
    ) -> bool:
        """
        Evaluates whether a listing satisfies candidate location or work mode preferences.
        Safely treats missing listing locations as non-matches when physical location is required.
        """
        valid_locs = [l.strip().lower() for l in (pref_locations or []) if l and l.strip()]
        valid_wms = [
            wm.value.lower() if hasattr(wm, "value") else str(wm).lower()
            for wm in (pref_work_modes or [])
        ]

        if not valid_locs and not valid_wms:
            return True

        norm_listing_locs = [l.strip().lower() for l in (listing_locations or []) if l and l.strip()]
        listing_wm_str = (
            listing_work_mode.value.lower()
            if hasattr(listing_work_mode, "value")
            else str(listing_work_mode).lower()
        )

        user_accepts_remote = ("remote" in valid_locs) or ("remote" in valid_wms) or (not valid_wms and not valid_locs)
        is_listing_remote = (listing_wm_str == "remote") or any("remote" in l for l in norm_listing_locs)

        if user_accepts_remote and is_listing_remote:
            return True

        if valid_wms and listing_wm_str != "unknown":
            if listing_wm_str != "flexible" and "flexible" not in valid_wms and listing_wm_str not in valid_wms:
                return False

        if valid_locs:
            if not norm_listing_locs:
                return False

            for pref_loc in valid_locs:
                if pref_loc == "remote":
                    continue
                aliases = {"bengaluru", "bangalore"} if pref_loc in {"bengaluru", "bangalore"} else {pref_loc}
                for list_loc in norm_listing_locs:
                    for alias in aliases:
                        if alias in list_loc:
                            return True
            return False

        return True

    def evaluate_observations(
        self,
        observations: Optional[List[Union[Listing, Dict[str, Any]]]],
        preference: Preference,
        profile: Optional[Profile] = None,
        attempt: int = 1,
        min_sufficient_count: int = 3,
        **kwargs: Any,
    ) -> ObservationEvaluation:
        """
        Evaluates retrieved internship observations against candidate target preferences
        and profile constraints, diagnosing insufficiency root causes.

        Parameters:
        - observations: Raw listings retrieved from search fetchers (Listing instances or dicts).
        - preference: Target candidate preferences (roles, domains, locations, work modes).
        - profile: Optional candidate profile for context.
        - attempt: Current search iteration attempt (1 to 3).
        - min_sufficient_count: Minimum threshold of relevant listings to consider observations sufficient.

        Returns:
        - ObservationEvaluation Pydantic model with structured diagnosis and replan recommendation.
        """
        if not isinstance(preference, Preference):
            raise TypeError(
                f"Expected preference to be an instance of Preference schema, got {type(preference).__name__}"
            )
        if profile is not None and not isinstance(profile, Profile):
            raise TypeError(
                f"Expected profile to be an instance of Profile schema or None, got {type(profile).__name__}"
            )

        try:
            int_attempt = int(attempt)
        except (ValueError, TypeError):
            int_attempt = 1
        clamped_attempt = max(1, min(int_attempt, 4))

        raw_items: List[Any]
        if observations is None:
            raw_items = []
        elif isinstance(observations, (list, tuple)):
            raw_items = list(observations)
        else:
            raw_items = [observations]

        total_observations = len(raw_items)

        if total_observations == 0:
            budget_exhausted = (clamped_attempt >= 4)
            reasons = ["No search results were retrieved from authorized sources."]
            if budget_exhausted:
                diagnosis = (
                    f"No listings found across target sources for attempt {clamped_attempt}. "
                    "Re-planning budget limit reached (4/4 attempts, 3/3 replan rounds completed); stopping search iterations."
                )
                recommend_replan = False
            else:
                diagnosis = (
                    f"No listings found across target sources for attempt {clamped_attempt}. "
                    "Re-planning recommended to adjust keywords or expand data sources."
                )
                recommend_replan = True

            return ObservationEvaluation(
                is_sufficient=False,
                total_observations=0,
                relevant_observations=0,
                reasons=reasons,
                diagnosis=diagnosis,
                recommend_replan=recommend_replan,
                relevant_listing_ids=[],
                unusable_count=0,
                role_match_count=0,
                domain_match_count=0,
                location_match_count=0,
            )

        unusable_count = 0
        role_match_count = 0
        domain_match_count = 0
        location_match_count = 0
        relevant_observations = 0
        relevant_listing_ids: List[str] = []

        for item in raw_items:
            norm = self._normalize_listing(item)
            if norm is None:
                unusable_count += 1
                continue

            matches_role = self._matches_role(norm["title"], norm["description"], preference.target_roles)
            matches_domain = self._matches_domain(
                norm["title"],
                norm["description"],
                norm["required_skills"] + norm["preferred_skills"],
                preference.domains,
            )
            matches_loc = self._matches_location(
                norm["locations"],
                norm["work_mode"],
                preference.locations,
                preference.work_modes,
            )

            if matches_role:
                role_match_count += 1
            if matches_domain:
                domain_match_count += 1
            if matches_loc:
                location_match_count += 1

            if matches_role and matches_domain and matches_loc:
                relevant_observations += 1
                relevant_listing_ids.append(norm["listing_id"])

        usable_count = total_observations - unusable_count
        is_sufficient = (relevant_observations >= min_sufficient_count)
        budget_exhausted = (clamped_attempt >= 4)

        reasons: List[str] = []
        if unusable_count > 0:
            reasons.append(
                f"{unusable_count} listing(s) were unusable or malformed (missing title or listing ID)."
            )

        if not is_sufficient:
            if preference.target_roles and role_match_count == 0 and usable_count > 0:
                reasons.append(f"No listings matched target roles: {', '.join(preference.target_roles)}.")
            if preference.domains and domain_match_count == 0 and usable_count > 0:
                reasons.append(f"No listings matched preferred domains: {', '.join(preference.domains)}.")
            if (preference.locations or preference.work_modes) and location_match_count == 0 and usable_count > 0:
                loc_summary = ", ".join(preference.locations) if preference.locations else "preferred work modes"
                reasons.append(f"No listings matched preferred locations ({loc_summary}).")

            if relevant_observations == 0 and usable_count > 0 and not any(r.startswith("No listings matched") for r in reasons):
                reasons.append("No listings satisfied role, domain, and location constraints simultaneously.")

            if 0 < relevant_observations < min_sufficient_count:
                reasons.append(
                    f"Too few relevant listings found ({relevant_observations} relevant out of {total_observations} total; minimum threshold is {min_sufficient_count})."
                )

        if is_sufficient:
            recommend_replan = False
            diagnosis = (
                f"Observations are sufficient: {relevant_observations} of {total_observations} "
                f"listings match user preferences (threshold: {min_sufficient_count})."
            )
        else:
            recommend_replan = not budget_exhausted
            if budget_exhausted:
                diagnosis = (
                    f"Observations are insufficient ({relevant_observations}/{total_observations} relevant), but execution "
                    f"budget cap reached ({clamped_attempt}/4 attempts, 3/3 replan rounds completed). Proceeding with available results."
                )
            else:
                diagnosis = (
                    f"Observations are insufficient ({relevant_observations}/{total_observations} relevant; required {min_sufficient_count}). "
                    f"Primary factors: {'; '.join(reasons)}. Re-planning recommended."
                )

        return ObservationEvaluation(
            is_sufficient=is_sufficient,
            total_observations=total_observations,
            relevant_observations=relevant_observations,
            reasons=reasons,
            diagnosis=diagnosis,
            recommend_replan=recommend_replan,
            relevant_listing_ids=relevant_listing_ids,
            unusable_count=unusable_count,
            role_match_count=role_match_count,
            domain_match_count=domain_match_count,
            location_match_count=location_match_count,
        )

    def evaluate_observation(
        self,
        observation: Any,
        preference: Preference,
        profile: Optional[Profile] = None,
        **kwargs: Any,
    ) -> ObservationEvaluation:
        """
        Singular convenience wrapper for evaluate_observations.
        """
        if observation is None:
            obs_list = []
        elif isinstance(observation, (list, tuple)):
            obs_list = list(observation)
        else:
            obs_list = [observation]
        return self.evaluate_observations(obs_list, preference, profile, **kwargs)

    def replan(
        self,
        current_plan: Union[SearchPlan, Dict[str, Any]],
        evaluation: ObservationEvaluation,
        preference: Preference,
        profile: Optional[Profile] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        Generates a progressively broader search plan when observation evaluation
        diagnoses insufficient opportunities, respecting candidate preferences and
        Section 10.6 execution limits.

        Parameters:
        - current_plan: Prior SearchPlan model instance or dictionary.
        - evaluation: Prior ObservationEvaluation result.
        - preference: Candidate target preference model (never mutated).
        - profile: Optional candidate profile model (never mutated).

        Returns:
        - A new SearchPlan dictionary with relaxed search criteria and incremented attempt,
          or None if observations are already sufficient, replanning limit is exhausted,
          or no safe relaxation is possible.
        """
        if not isinstance(evaluation, ObservationEvaluation):
            raise TypeError(
                f"Expected evaluation to be an instance of ObservationEvaluation, got {type(evaluation).__name__}"
            )
        if not isinstance(preference, Preference):
            raise TypeError(
                f"Expected preference to be an instance of Preference, got {type(preference).__name__}"
            )
        if profile is not None and not isinstance(profile, Profile):
            raise TypeError(
                f"Expected profile to be an instance of Profile or None, got {type(profile).__name__}"
            )

        if isinstance(current_plan, SearchPlan):
            plan_dict = current_plan.model_dump()
            current_attempt = current_plan.attempt
        elif isinstance(current_plan, dict):
            plan_dict = dict(current_plan)
            try:
                current_attempt = int(plan_dict.get("attempt", 1))
            except (ValueError, TypeError):
                current_attempt = 1
        else:
            raise TypeError(
                f"Expected current_plan to be SearchPlan or dict, got {type(current_plan).__name__}"
            )

        # 1. If observations are already sufficient, do not generate another plan
        if evaluation.is_sufficient:
            return None

        # 2. If evaluation recommends no further replanning, do not generate another plan
        if not evaluation.recommend_replan:
            return None

        # 3. Maximum attempt limit: cannot exceed 4 total attempts (1 initial + 3 replans)
        if current_attempt >= 4:
            return None

        next_attempt = current_attempt + 1
        if next_attempt > 4:
            return None

        new_filters = copy.deepcopy(plan_dict.get("filters", {}))
        new_target_sources = list(plan_dict.get("target_sources", self.default_sources))
        new_relaxation_notes = list(plan_dict.get("relaxation_notes", []))

        constraints = preference.constraints or {}
        is_hard_location = bool(constraints.get("hard_location") or constraints.get("strict_location"))
        is_hard_domain = bool(constraints.get("hard_domain") or constraints.get("strict_domain"))
        # Stipend relaxation is strictly opt-in: default is to preserve min_stipend
        stipend_relaxation_permitted = (
            constraints.get("flexible_stipend") is True or
            constraints.get("allow_unpaid") is True
        )

        # Relaxation order across 3 re-planning rounds (Attempts 2, 3, 4):
        # Attempt 2 (Replan 1): Broaden locations (while retaining work-mode restrictions)
        # Attempt 3 (Replan 2): Broaden domains (while retaining target roles)
        # Attempt 4 (Replan 3): Broaden queries & stipend (if explicitly permitted)
        if next_attempt == 2:
            # Step 1: Broaden locations
            if new_filters.get("locations") and not is_hard_location:
                old_locs = list(new_filters["locations"])
                new_filters["locations"] = []
                work_mode_desc = [
                    wm.value if hasattr(wm, "value") else str(wm)
                    for wm in (preference.work_modes or [])
                ]
                new_relaxation_notes.append(
                    f"Attempt 2: Relaxed strict location filter from {old_locs} to open location "
                    f"compatible with work modes ({work_mode_desc})."
                )
            else:
                # If location could not be relaxed, try domain or stipend (if permitted)
                if preference.domains and not is_hard_domain and not any("domain" in n.lower() for n in new_relaxation_notes):
                    new_relaxation_notes.append(
                        f"Attempt 2: Relaxed domain constraint from {list(preference.domains)} "
                        f"to broader tech opportunities for target roles."
                    )
                elif new_filters.get("min_stipend") is not None and stipend_relaxation_permitted:
                    old_stipend = new_filters["min_stipend"]
                    new_filters["min_stipend"] = None
                    new_relaxation_notes.append(
                        f"Attempt 2: Relaxed minimum stipend filter from {old_stipend} to None (user permitted flexible stipend)."
                    )

        elif next_attempt == 3:
            # Step 2: Broaden domains while retaining target roles
            domain_relaxed_now = False
            if preference.domains and not is_hard_domain and not any("domain" in n.lower() for n in new_relaxation_notes):
                new_relaxation_notes.append(
                    f"Attempt 3: Relaxed domain constraint from {list(preference.domains)} "
                    f"to all industry domains for target roles."
                )
                domain_relaxed_now = True

            # Step 2b: If location was not yet relaxed and is not hard
            loc_relaxed_now = False
            if new_filters.get("locations") and not is_hard_location:
                old_locs = list(new_filters["locations"])
                new_filters["locations"] = []
                new_relaxation_notes.append(
                    f"Attempt 3: Relaxed strict location filter from {old_locs} to open location."
                )
                loc_relaxed_now = True

            # Step 2c: If neither domain nor location could be relaxed in this step or previously,
            # and stipend relaxation is explicitly permitted, relax stipend
            if not domain_relaxed_now and not loc_relaxed_now:
                if new_filters.get("min_stipend") is not None and stipend_relaxation_permitted and not any("stipend" in n.lower() for n in new_relaxation_notes):
                    old_stipend = new_filters["min_stipend"]
                    new_filters["min_stipend"] = None
                    new_relaxation_notes.append(
                        f"Attempt 3: Relaxed minimum stipend filter from {old_stipend} to None (user permitted flexible stipend)."
                    )

        elif next_attempt == 4:
            # Step 3 (Final Replan): Broaden stipend (if permitted) and expand query variations
            if new_filters.get("min_stipend") is not None and stipend_relaxation_permitted and not any("stipend" in n.lower() for n in new_relaxation_notes):
                old_stipend = new_filters["min_stipend"]
                new_filters["min_stipend"] = None
                new_relaxation_notes.append(
                    f"Attempt 4: Relaxed minimum stipend filter from {old_stipend} to None (user permitted flexible stipend)."
                )

            if preference.domains and not is_hard_domain and not any("domain" in n.lower() for n in new_relaxation_notes):
                new_relaxation_notes.append(
                    f"Attempt 4: Relaxed domain constraint from {list(preference.domains)} "
                    f"to all industry domains for target roles."
                )

            if new_filters.get("locations") and not is_hard_location:
                old_locs = list(new_filters["locations"])
                new_filters["locations"] = []
                new_relaxation_notes.append(
                    f"Attempt 4: Relaxed strict location filter from {old_locs} to open location."
                )

            new_relaxation_notes.append(
                "Attempt 4: Expanded search query variations for target roles across all authorized sources."
            )

        # Generate queries for the relaxed plan
        # Target roles are ALWAYS strictly preserved from user preference
        raw_roles = [r.strip() for r in (preference.target_roles or []) if r and r.strip()]
        roles = raw_roles or ["Software Engineering Intern"]

        # Check if domain relaxation occurred
        domain_relaxed = any("domain" in n.lower() for n in new_relaxation_notes)
        if domain_relaxed:
            active_domains = []
        else:
            active_domains = [d.strip() for d in (preference.domains or []) if d and d.strip()]

        skills = [s.strip() for s in (profile.skills or []) if s and s.strip()] if profile else []

        if domain_relaxed:
            pref_for_kw = Preference(
                profile_id=preference.profile_id,
                target_roles=roles,
                domains=[],
                locations=new_filters.get("locations", []),
                work_modes=preference.work_modes,
                min_stipend=new_filters.get("min_stipend"),
            )
            query_keywords = self._extract_keywords(pref_for_kw, profile)
            search_queries = self._generate_queries(roles, [], skills)
            for role in roles:
                for modifier in ["Internship", "Junior"]:
                    mod_q = f"{role} {modifier}" if modifier.lower() not in role.lower() else role
                    if mod_q not in search_queries:
                        search_queries.append(mod_q)
            if next_attempt == 4:
                for role in roles:
                    for modifier in ["Associate", "Trainee"]:
                        mod_q = f"{modifier} {role}" if modifier.lower() not in role.lower() else role
                        if mod_q not in search_queries:
                            search_queries.append(mod_q)
        else:
            query_keywords = self._extract_keywords(preference, profile)
            search_queries = self._generate_queries(roles, active_domains, skills)
            if next_attempt == 4:
                for role in roles:
                    for modifier in ["Internship", "Junior"]:
                        mod_q = f"{role} {modifier}" if modifier.lower() not in role.lower() else role
                        if mod_q not in search_queries:
                            search_queries.append(mod_q)

        # Check whether any actual relaxation occurred
        num_new_notes = len(new_relaxation_notes) - len(plan_dict.get("relaxation_notes", []))
        filters_changed = (new_filters != plan_dict.get("filters", {}))
        queries_changed = (set(search_queries) != set(plan_dict.get("search_queries", [])))
        actual_relaxation_occurred = (num_new_notes > 0 or filters_changed or queries_changed)

        if not actual_relaxation_occurred:
            return None

        new_plan_id = f"plan_{uuid.uuid4().hex[:8]}_att{next_attempt}"
        new_plan = SearchPlan(
            plan_id=new_plan_id,
            attempt=next_attempt,
            target_sources=new_target_sources,
            query_keywords=query_keywords,
            search_queries=search_queries,
            filters=new_filters,
            relaxed_mode=True,
            relaxation_notes=new_relaxation_notes,
        )

        return new_plan.model_dump()

    relax_plan = replan
