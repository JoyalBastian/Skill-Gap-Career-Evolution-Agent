"""Gemini-only learning recommendation engine.

LearningResource rows are no longer seeded from JSON. Gemini suggests
resources per user; we save them with is_ai_generated=True so the UI can warn
the user that URLs may need verification.
"""
from __future__ import annotations

import logging
import threading

from django.db.models import Q

from ai_engine.llm_client import GeminiUnavailable, LLMUnavailable, active_provider, chat_json
from apps.analytics.models import AIInsight
from apps.careers.models import CareerPrediction, SkillGapReport
from apps.recommendations.models import LearningResource, Recommendation
from apps.skills.models import Skill
from apps.users.models import Profile
from services.skill_level_utils import enrich_gap_context, level_display_label, next_level, normalize_course_level
from services.skill_utils import ensure_skill
from services.user_understanding_service import UserUnderstandingService

logger = logging.getLogger(__name__)

VALID_TYPES = {"course", "certification", "project", "technology", "book"}
VALID_LEVELS = {"beginner", "intermediate", "advanced"}

LEVEL_FILTER_OPTIONS = [
    ("", "All"),
    ("beginner", "Beginner"),
    ("intermediate", "Intermediate"),
    ("advanced", "Expert"),
]


def _normalize_level(raw: str | None) -> str:
    level = (raw or "beginner").strip().lower()
    if level in ("expert", "experts", "expertise"):
        return "advanced"
    if level not in VALID_LEVELS:
        return "beginner"
    return level


def level_display_label(level: str) -> str:
    normalized = _normalize_level(level)
    for value, label in LEVEL_FILTER_OPTIONS:
        if value == normalized:
            return label
    return normalized.title()

_rec_lock = threading.Lock()
_rec_running_users: set[int] = set()


def _valid_url(url: str) -> str:
    u = (url or "").strip()
    if u.startswith("https://"):
        return u
    return ""


class RecommendationService:
    def default_top_n(self) -> int:
        return 4 if active_provider() == "ollama" else 12

    def _ollama_max_tokens(self, top_n: int) -> int:
        # Large JSON payloads; 2048 often truncates mid-array on local models.
        return 4096 if top_n > 3 else 3072

    def _is_truncated_json_error(self, exc: BaseException) -> bool:
        msg = str(exc).lower()
        return "valid json" in msg or "truncated" in msg

    def is_generating(self, user_id: int) -> bool:
        with _rec_lock:
            return user_id in _rec_running_users

    def has_gap_targeted_recommendations(self, user_id: int) -> bool:
        return Recommendation.objects.filter(
            user_id=user_id,
            target_skill__isnull=False,
        ).exists()

    def start_generate_async(self, user_id: int, *, force: bool = False) -> str:
        """
        Generate recommendations in a background thread.
        Returns: 'done', 'running', or 'started'.
        """
        if not force and self.has_gap_targeted_recommendations(user_id):
            return "done"

        with _rec_lock:
            if user_id in _rec_running_users:
                return "running"
            _rec_running_users.add(user_id)

        def _run() -> None:
            try:
                self.generate_gap_targeted_courses(user_id)
            except Exception:
                logger.exception(
                    "Background recommendation generation failed for user=%s",
                    user_id,
                )
            finally:
                with _rec_lock:
                    _rec_running_users.discard(user_id)

        threading.Thread(
            target=_run,
            daemon=True,
            name=f"recs-{user_id}",
        ).start()
        return "started"

    def generate_recommendations(
        self,
        user_id: int,
        top_n: int | None = None,
    ) -> list[Recommendation]:
        top_n = top_n or self.default_top_n()
        profile_text = UserUnderstandingService().get_user_profile_text(user_id)

        profile = Profile.objects.filter(user_id=user_id).first()
        user_level = (profile.target_career_level if profile else "beginner") or "beginner"

        gap_report = SkillGapReport.objects.filter(user_id=user_id).first()
        gap_skills: list[str] = []
        if gap_report:
            for s in gap_report.prioritized_skills or []:
                name = s.get("skill") or s.get("skill_name")
                if name:
                    gap_skills.append(name)

        gap_section = ""
        if gap_skills:
            gap_section = (
                "TOP SKILL GAPS (each recommendation MUST help close at least one):\n"
                + ", ".join(gap_skills[:8])
                + "\n\n"
            )

        def build_prompt(n: int) -> str:
            return (
                "You are a learning coach. Recommend personalized learning resources for the user.\n\n"
                "STRICT RULES:\n"
                f"1. Return EXACTLY {n} items in the recommendations array.\n"
                "2. Each item must address at least one of the user's gap skills (if listed).\n"
                "3. reason must cite a specific user need (gap skill, career goal, or profile fact).\n"
                "4. title max 80 characters; description max 120 characters; score between 0 and 1.\n"
            "5. resource_type must be one of: course, certification, project, technology, book.\n"
            "6. level must be one of: beginner, intermediate, advanced (use advanced for expert-level content).\n"
            "7. Only include url if you are confident it is a real https URL; otherwise use empty string.\n"
            "8. Do not duplicate titles.\n\n"
                f"USER PROFILE:\n{profile_text}\n\n"
                f"USER LEVEL: {user_level}\n"
                f"{gap_section}"
                f"Return EXACTLY {n} resources mixing courses, certifications, projects and books.\n"
                "Respond ONLY with a JSON object:\n"
                "{\n"
                "  \"recommendations\": [\n"
                "    {\n"
                "      \"title\": \"resource title\",\n"
                "      \"resource_type\": \"course|certification|project|technology|book\",\n"
                "      \"level\": \"beginner|intermediate|advanced\",\n"
                "      \"description\": \"short summary\",\n"
                "      \"url\": \"https://... or empty\",\n"
                "      \"skills\": [\"skills covered\"],\n"
                "      \"score\": number from 0 to 1,\n"
                "      \"reason\": \"why this fits this user\"\n"
                "    }\n"
                "  ]\n"
                "}"
            )

        attempt_counts = [top_n]
        if active_provider() == "ollama" and top_n > 3:
            attempt_counts.append(3)

        data = None
        last_exc: BaseException | None = None
        for attempt_n in attempt_counts:
            max_tokens = (
                self._ollama_max_tokens(attempt_n)
                if active_provider() == "ollama"
                else 3072
            )
            try:
                data = chat_json(build_prompt(attempt_n), max_output_tokens=max_tokens)
                top_n = attempt_n
                break
            except LLMUnavailable as e:
                last_exc = e
                if not self._is_truncated_json_error(e):
                    raise
                logger.warning(
                    "Recommendations JSON truncated (n=%s); retrying with fewer items.",
                    attempt_n,
                )
        if data is None:
            raise last_exc or GeminiUnavailable("Failed to generate recommendations.")
        items = []
        if isinstance(data, dict):
            items = data.get("recommendations") or []
        elif isinstance(data, list):
            items = data
        if not items:
            raise GeminiUnavailable("Gemini returned no recommendations.")

        Recommendation.objects.filter(user_id=user_id).delete()

        created: list[Recommendation] = []
        seen_keys: set[tuple[str, str]] = set()

        for item in items:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()[:120]
            if not title:
                continue

            rtype = item.get("resource_type") or "course"
            if rtype not in VALID_TYPES:
                rtype = "course"
            level = _normalize_level(item.get("level"))

            dedupe_key = (title.lower(), rtype)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)

            score = float(item.get("score") or 0.5)
            score = max(0.0, min(1.0, score))
            url = _valid_url(item.get("url") or "")
            reason = item.get("reason") or ""

            resource, _ = LearningResource.objects.get_or_create(
                title=title[:255],
                resource_type=rtype,
                defaults={
                    "description": (item.get("description") or "")[:2000],
                    "url": url,
                    "level": level,
                    "is_ai_generated": True,
                },
            )
            updated = False
            if not resource.description and item.get("description"):
                resource.description = item["description"][:2000]
                updated = True
            if not resource.url and url:
                resource.url = url
                updated = True
            if resource.level != level:
                resource.level = level
                updated = True
            if updated:
                resource.save(update_fields=["description", "url", "level"])

            for skill_name in item.get("skills") or []:
                sk = ensure_skill(str(skill_name).strip())
                if sk:
                    resource.skills.add(sk)

            rec = Recommendation.objects.create(
                user_id=user_id,
                resource=resource,
                category=rtype,
                title=resource.title,
                description=resource.description,
                url=resource.url,
                score=round(score, 3),
                reason=reason,
            )
            created.append(rec)
            if len(created) >= top_n:
                break

        if not created:
            raise GeminiUnavailable("No valid recommendations after validation.")

        AIInsight.objects.create(
            user_id=user_id,
            insight_type="recommendation",
            payload={"count": len(created)},
        )
        return created

    def _courses_per_skill(self) -> int:
        return 2 if active_provider() == "ollama" else 3

    def _gap_skills_limit(self) -> int:
        return 3 if active_provider() == "ollama" else 5

    def _save_recommendation_item(
        self,
        user_id: int,
        item: dict,
        *,
        target_skill: Skill | None = None,
        gap_report: SkillGapReport | None = None,
        career_id: int | None = None,
        seen_keys: set[tuple[str, str]] | None = None,
        is_primary_for_gap: bool = False,
        forced_level: str | None = None,
    ) -> Recommendation | None:
        title = (item.get("title") or "").strip()[:120]
        if not title:
            return None

        rtype = item.get("resource_type") or "course"
        if rtype not in VALID_TYPES:
            rtype = "course"
        level = _normalize_level(forced_level or item.get("level"))

        dedupe_key = (title.lower(), rtype)
        if seen_keys is not None:
            if dedupe_key in seen_keys:
                return None
            seen_keys.add(dedupe_key)

        score = float(item.get("score") or 0.5)
        score = max(0.0, min(1.0, score))
        url = _valid_url(item.get("url") or "")
        reason = item.get("reason") or ""

        resource, _ = LearningResource.objects.get_or_create(
            title=title[:255],
            resource_type=rtype,
            defaults={
                "description": (item.get("description") or "")[:2000],
                "url": url,
                "level": level,
                "is_ai_generated": True,
            },
        )
        updated = False
        if not resource.description and item.get("description"):
            resource.description = item["description"][:2000]
            updated = True
        if not resource.url and url:
            resource.url = url
            updated = True
        if resource.level != level:
            resource.level = level
            updated = True
        if updated:
            resource.save(update_fields=["description", "url", "level"])

        skill_names = list(item.get("skills") or [])
        if target_skill:
            skill_names.insert(0, target_skill.name)
        for skill_name in skill_names:
            sk = ensure_skill(str(skill_name).strip())
            if sk:
                resource.skills.add(sk)

        return Recommendation.objects.create(
            user_id=user_id,
            resource=resource,
            career_id=career_id,
            target_skill=target_skill,
            gap_report=gap_report,
            category=rtype,
            title=resource.title,
            description=resource.description,
            url=resource.url,
            score=round(score, 3),
            reason=reason,
            is_primary_for_gap=is_primary_for_gap,
        )

    def _course_levels_for_gap(self, gap_context: dict) -> list[tuple[str, bool, float]]:
        """Return [(level, is_primary, score), ...] for level-aware generation."""
        ctx = enrich_gap_context(gap_context)
        start = ctx["recommended_start_level"]
        nxt = ctx.get("next_level")
        user_prof = ctx["user_proficiency"]
        req_prof = ctx["required_proficiency"]

        levels: list[tuple[str, bool, float]] = [(start, True, 0.9)]
        if nxt:
            levels.append((nxt, False, 0.75))
        gap = req_prof - user_prof
        if (
            active_provider() != "ollama"
            and gap >= 3
            and nxt
            and nxt != "advanced"
        ):
            advanced = next_level(nxt)
            if advanced:
                levels.append((advanced, False, 0.65))
        return levels

    def _gap_generation_context(
        self,
        user_id: int,
        top_n_skills: int | None = None,
    ) -> tuple[CareerPrediction, SkillGapReport, str, dict[str, dict], list[tuple[Skill, dict]]] | None:
        """Resolve prediction, gap report, and skill entries for course generation."""
        top_n_skills = top_n_skills or self._gap_skills_limit()
        prediction = CareerPrediction.objects.filter(user_id=user_id).order_by("rank").first()
        if not prediction:
            return None

        gap_report = SkillGapReport.objects.filter(
            user_id=user_id,
            career_id=prediction.career_id,
        ).order_by("-created_at").first()
        if not gap_report or not (gap_report.missing_skills or gap_report.prioritized_skills):
            return None

        missing_by_slug: dict[str, dict] = {}
        for item in gap_report.missing_skills or []:
            slug = (item.get("slug") or "").strip()
            if slug:
                missing_by_slug[slug] = item

        skill_entries: list[tuple[Skill, dict]] = []
        for entry in (gap_report.prioritized_skills or [])[:top_n_skills]:
            slug = (entry.get("slug") or "").strip()
            skill_name = entry.get("skill") or entry.get("skill_name") or slug
            if not slug:
                continue
            skill = Skill.objects.filter(slug=slug).first()
            if not skill:
                skill = ensure_skill(skill_name, slug)
            if not skill:
                continue
            skill_entries.append((skill, enrich_gap_context(missing_by_slug.get(slug, entry))))

        if not skill_entries:
            return None

        return prediction, gap_report, prediction.career.name, missing_by_slug, skill_entries

    def _save_skill_placeholders(
        self,
        user_id: int,
        skill: Skill,
        gap_context: dict,
        *,
        gap_report: SkillGapReport,
        career_id: int | None,
        seen_keys: set[tuple[str, str]],
    ) -> list[Recommendation]:
        """Create instant placeholder courses for one skill (no LLM)."""
        level_plan = self._course_levels_for_gap(gap_context)
        skill_name = skill.display_name if hasattr(skill, "display_name") else skill.name
        created: list[Recommendation] = []
        for planned_level, is_primary, planned_score in level_plan:
            label = level_display_label(planned_level)
            item = {
                "title": f"{skill_name} — {label} Learning Path",
                "resource_type": "course",
                "description": (
                    f"{label} learning suggestions to build {skill_name} for your target career."
                ),
                "reason": (
                    f"Suggested {label.lower()} resources to close your {skill_name} skill gap."
                ),
                "level": planned_level,
                "score": planned_score,
            }
            rec = self._save_recommendation_item(
                user_id,
                item,
                target_skill=skill,
                gap_report=gap_report,
                career_id=career_id,
                seen_keys=seen_keys,
                is_primary_for_gap=is_primary,
                forced_level=planned_level,
            )
            if rec:
                created.append(rec)
        return created

    def create_placeholder_gap_courses(
        self,
        user_id: int,
        top_n_skills: int | None = None,
    ) -> dict[str, list[int]]:
        """Instant gap-targeted placeholder courses so the UI is usable without waiting for the LLM."""
        ctx = self._gap_generation_context(user_id, top_n_skills)
        if not ctx:
            return {}

        _prediction, gap_report, _career_name, _missing, skill_entries = ctx
        career_id = gap_report.career_id
        seen_keys: set[tuple[str, str]] = set()
        result: dict[str, list[int]] = {}
        new_rec_ids: list[int] = []

        for skill, gap_context in skill_entries:
            created = self._save_skill_placeholders(
                user_id,
                skill,
                gap_context,
                gap_report=gap_report,
                career_id=career_id,
                seen_keys=seen_keys,
            )
            if created:
                result[skill.slug] = [r.id for r in created]
                new_rec_ids.extend(r.id for r in created)

        if new_rec_ids:
            Recommendation.objects.filter(user_id=user_id).exclude(
                id__in=new_rec_ids
            ).delete()
        return result

    def _apply_skill_course_items(
        self,
        user_id: int,
        skill: Skill,
        gap_context: dict,
        items: list,
        *,
        gap_report: SkillGapReport,
        career_id: int | None,
        seen_keys: set[tuple[str, str]],
    ) -> list[Recommendation]:
        level_plan = self._course_levels_for_gap(gap_context)
        skill_name = skill.display_name if hasattr(skill, "display_name") else skill.name
        created: list[Recommendation] = []
        for i, (planned_level, is_primary, planned_score) in enumerate(level_plan):
            raw = items[i] if i < len(items) else None
            item = dict(raw) if isinstance(raw, dict) else {}
            item["level"] = planned_level
            item["score"] = planned_score
            if not (item.get("title") or "").strip():
                label = level_display_label(planned_level)
                item["title"] = f"{skill_name} — {label} Learning Path"
                item.setdefault("resource_type", "course")
                item.setdefault(
                    "description",
                    f"{label} learning suggestions to build {skill_name} for your target career.",
                )
                item.setdefault(
                    "reason",
                    f"Suggested {label.lower()} resources to close your {skill_name} skill gap.",
                )
            rec = self._save_recommendation_item(
                user_id,
                item,
                target_skill=skill,
                gap_report=gap_report,
                career_id=career_id,
                seen_keys=seen_keys,
                is_primary_for_gap=is_primary,
                forced_level=planned_level,
            )
            if rec:
                created.append(rec)
        return created

    def _generate_gap_courses_batch(
        self,
        user_id: int,
        skill_entries: list[tuple[Skill, dict]],
        *,
        gap_report: SkillGapReport,
        career_name: str,
        seen_keys: set[tuple[str, str]],
    ) -> tuple[dict[str, list[int]], list[int]]:
        """One LLM call for all gap skills (much faster than per-skill calls)."""
        profile_text = UserUnderstandingService().get_user_profile_text(user_id)
        if active_provider() == "ollama":
            profile_text = profile_text[:1200]

        skill_blocks: list[str] = []
        total_items = 0
        for skill, gap_context in skill_entries:
            ctx = enrich_gap_context(gap_context)
            level_plan = self._course_levels_for_gap(ctx)
            total_items += len(level_plan)
            skill_name = skill.display_name if hasattr(skill, "display_name") else skill.name
            levels = ", ".join(
                f"{level_display_label(lvl)}{' (primary)' if primary else ''}"
                for lvl, primary, _score in level_plan
            )
            skill_blocks.append(
                f"- skill_slug: {skill.slug}\n"
                f"  skill_name: {skill_name}\n"
                f"  user_proficiency: {ctx['user_proficiency']}/5\n"
                f"  required_proficiency: {ctx['required_proficiency']}/5\n"
                f"  levels ({len(level_plan)} items): {levels}"
            )

        prompt = (
            "You are a learning coach. Suggest courses/certifications for ALL listed skill gaps.\n\n"
            "STRICT RULES:\n"
            f"1. Return exactly one entry per skill_slug below ({len(skill_entries)} skills).\n"
            "2. Each skill entry must include the exact number of recommendations for its levels.\n"
            "3. resource_type must be course or certification.\n"
            "4. title max 80 chars; description max 120 chars; reason max 150 chars.\n"
            "5. Use the exact level assigned for each item.\n"
            "6. url only if confident https URL exists; otherwise empty string.\n"
            "7. Do not duplicate titles across skills.\n\n"
            f"TARGET CAREER: {career_name}\n"
            f"USER PROFILE (summary):\n{profile_text}\n\n"
            "SKILL GAPS:\n"
            + "\n".join(skill_blocks)
            + "\n\nRespond ONLY with JSON:\n"
            "{\n"
            '  "skill_courses": [\n'
            "    {\n"
            '      "skill_slug": "slug_here",\n'
            '      "recommendations": [\n'
            "        {\n"
            '          "title": "resource title",\n'
            '          "resource_type": "course",\n'
            '          "level": "beginner|intermediate|advanced",\n'
            '          "description": "short summary",\n'
            '          "url": "",\n'
            '          "score": 0.9,\n'
            '          "reason": "why this helps"\n'
            "        }\n"
            "      ]\n"
            "    }\n"
            "  ]\n"
            "}"
        )

        max_tokens = 4096 if active_provider() == "ollama" else 6144
        data = chat_json(prompt, max_output_tokens=max_tokens)
        by_slug: dict[str, list] = {}
        if isinstance(data, dict):
            for block in data.get("skill_courses") or []:
                if isinstance(block, dict):
                    slug = (block.get("skill_slug") or "").strip()
                    if slug:
                        by_slug[slug] = block.get("recommendations") or []

        career_id = gap_report.career_id
        result: dict[str, list[int]] = {}
        new_rec_ids: list[int] = []

        for skill, gap_context in skill_entries:
            items = by_slug.get(skill.slug, [])
            created = self._apply_skill_course_items(
                user_id,
                skill,
                gap_context,
                items,
                gap_report=gap_report,
                career_id=career_id,
                seen_keys=seen_keys,
            )
            if created:
                result[skill.slug] = [r.id for r in created]
                new_rec_ids.extend(r.id for r in created)

        return result, new_rec_ids

    def generate_courses_for_skill(
        self,
        user_id: int,
        skill: Skill,
        gap_context: dict,
        *,
        gap_report: SkillGapReport | None = None,
        career_name: str = "",
        user_level: str = "beginner",
        seen_keys: set[tuple[str, str]] | None = None,
    ) -> list[Recommendation]:
        """Generate AI learning recommendations per gap level (beginner / intermediate / expert)."""
        ctx = enrich_gap_context(gap_context)
        level_plan = self._course_levels_for_gap(ctx)
        n = len(level_plan)
        profile_text = UserUnderstandingService().get_user_profile_text(user_id)
        skill_name = skill.display_name if hasattr(skill, "display_name") else skill.name
        explanation = (ctx.get("explanation") or "").strip()
        user_prof = ctx["user_proficiency"]
        req_prof = ctx["required_proficiency"]
        start_level = ctx["recommended_start_level"]
        next_lvl = ctx.get("next_level")

        level_rules = "\n".join(
            f"   - Item {i + 1}: level MUST be \"{lvl}\" (score ~{score}); "
            f"{'PRIMARY — best starting point' if primary else 'progression — next step up'}"
            for i, (lvl, primary, score) in enumerate(level_plan)
        )

        gap_detail = (
            f"SKILL GAP TO CLOSE: {skill_name}\n"
            f"CURRENT PROFICIENCY: {user_prof}/5\n"
            f"REQUIRED FOR CAREER: {req_prof}/5\n"
            f"RECOMMENDED START LEVEL: {start_level} ({level_display_label(start_level)})\n"
        )
        if next_lvl:
            gap_detail += f"NEXT LEVEL: {next_lvl} ({level_display_label(next_lvl)})\n"
        if explanation:
            gap_detail += f"Why it matters: {explanation}\n"

        prompt = (
            "You are a learning coach. Suggest learning resources to help the user close ONE skill gap.\n\n"
            "STRICT RULES:\n"
            f"1. Return EXACTLY {n} items in the recommendations array.\n"
            f"2. Every item MUST address {skill_name} specifically.\n"
            "3. Prefer resource_type course or certification.\n"
            "4. reason must explain how this resource closes the named skill gap at its level.\n"
            "5. title max 80 characters; description max 120 characters.\n"
            "6. Each item MUST use the exact level assigned below.\n"
            "7. Only include url if you are confident it is a real https URL; otherwise use empty string.\n"
            "8. Do not duplicate titles.\n"
            "9. REQUIRED LEVELS PER ITEM:\n"
            f"{level_rules}\n\n"
            f"TARGET CAREER: {career_name or 'user target role'}\n"
            f"{gap_detail}\n"
            f"USER PROFILE:\n{profile_text}\n\n"
            "Respond ONLY with a JSON object:\n"
            "{\n"
            '  "recommendations": [\n'
            "    {\n"
            '      "title": "resource title",\n'
            '      "resource_type": "course|certification",\n'
            '      "level": "beginner|intermediate|advanced",\n'
            '      "description": "short summary",\n'
            '      "url": "https://... or empty",\n'
            f'      "skills": ["{skill_name}"],\n'
            '      "score": number from 0 to 1,\n'
            '      "reason": "why this closes the skill gap at this level"\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        max_tokens = 2048 if active_provider() == "ollama" else 3072
        items: list = []
        try:
            data = chat_json(prompt, max_output_tokens=max_tokens)
            if isinstance(data, dict):
                items = data.get("recommendations") or []
            elif isinstance(data, list):
                items = data
        except LLMUnavailable as e:
            logger.warning(
                "LLM course generation failed for skill=%s; using placeholders: %s",
                skill_name,
                e,
            )

        career_id = gap_report.career_id if gap_report else None
        created = self._apply_skill_course_items(
            user_id,
            skill,
            ctx,
            items,
            gap_report=gap_report,
            career_id=career_id,
            seen_keys=seen_keys or set(),
        )
        return created

    def generate_gap_targeted_courses(
        self,
        user_id: int,
        top_n_skills: int | None = None,
    ) -> dict[str, list[int]]:
        """
        Generate courses per priority gap skill from the latest gap report.
        Uses one batched LLM call when possible (faster than per-skill calls).
        """
        ctx = self._gap_generation_context(user_id, top_n_skills)
        if not ctx:
            logger.info("No skill gaps for user %s; skipping gap courses.", user_id)
            return {}

        _prediction, gap_report, career_name, _missing, skill_entries = ctx
        seen_keys: set[tuple[str, str]] = set()
        result: dict[str, list[int]] = {}
        new_rec_ids: list[int] = []

        try:
            result, new_rec_ids = self._generate_gap_courses_batch(
                user_id,
                skill_entries,
                gap_report=gap_report,
                career_name=career_name,
                seen_keys=seen_keys,
            )
        except LLMUnavailable as e:
            logger.warning(
                "Batch course generation failed for user=%s; falling back per skill: %s",
                user_id,
                e,
            )
            for skill, gap_context in skill_entries:
                try:
                    created = self.generate_courses_for_skill(
                        user_id,
                        skill,
                        gap_context,
                        gap_report=gap_report,
                        career_name=career_name,
                        seen_keys=seen_keys,
                    )
                except LLMUnavailable as skill_exc:
                    logger.warning(
                        "Gap course generation failed for user=%s skill=%s: %s",
                        user_id,
                        skill.slug,
                        skill_exc,
                    )
                    created = self._save_skill_placeholders(
                        user_id,
                        skill,
                        gap_context,
                        gap_report=gap_report,
                        career_id=gap_report.career_id,
                        seen_keys=seen_keys,
                    )
                if created:
                    result[skill.slug] = [r.id for r in created]
                    new_rec_ids.extend(r.id for r in created)

        if new_rec_ids:
            Recommendation.objects.filter(user_id=user_id).exclude(
                id__in=new_rec_ids
            ).delete()
            AIInsight.objects.create(
                user_id=user_id,
                insight_type="recommendation",
                payload={"count": len(new_rec_ids), "gap_targeted": True, "batched": True},
            )
        else:
            logger.warning(
                "No gap-targeted courses created for user=%s; keeping existing recommendations.",
                user_id,
            )

        return result

    def get_courses_preview_by_slug(
        self,
        user_id: int,
        slugs: list[str],
        limit_per_skill: int = 2,
    ) -> dict[str, list[Recommendation]]:
        """Top N gap-targeted courses per skill slug for the gap list UI."""
        previews: dict[str, list[Recommendation]] = {}
        for slug in slugs:
            if not slug:
                continue
            previews[slug] = list(
                Recommendation.objects.filter(
                    user_id=user_id,
                    target_skill__slug=slug,
                )
                .select_related("resource", "target_skill")
                .order_by("-score")[:limit_per_skill]
            )
        return previews

    def get_grouped_by_gap_skill(self, user_id: int) -> list[dict]:
        """Recommendations grouped by target gap skill, with level sub-groups."""
        recs = (
            Recommendation.objects.filter(user_id=user_id, target_skill__isnull=False)
            .select_related("resource", "target_skill")
            .order_by("target_skill__name", "-score")
        )
        level_order = ["beginner", "intermediate", "advanced"]
        groups: dict[str, dict] = {}
        for rec in recs:
            slug = rec.target_skill.slug
            if slug not in groups:
                groups[slug] = {
                    "skill": rec.target_skill,
                    "recommendations": [],
                    "level_groups": [],
                }
            groups[slug]["recommendations"].append(rec)
            lvl = rec.resource.level if rec.resource else "beginner"
            level_groups: dict[str, list] = {}
            for r in groups[slug]["recommendations"]:
                r_lvl = r.resource.level if r.resource else "beginner"
                level_groups.setdefault(r_lvl, []).append(r)
            groups[slug]["level_groups"] = [
                {
                    "level": lvl_key,
                    "label": level_display_label(lvl_key),
                    "recommendations": level_groups[lvl_key],
                }
                for lvl_key in level_order
                if lvl_key in level_groups
            ]
        return list(groups.values())

    def get_user_recommendations(
        self,
        user_id: int,
        category: str | None = None,
        level: str | None = None,
    ):
        qs = Recommendation.objects.filter(user_id=user_id).select_related("resource")
        if category:
            qs = qs.filter(category=category)
        if level:
            qs = qs.filter(resource__level=_normalize_level(level))
        return qs.order_by("-score")

    def get_recommendations_for_skill(
        self,
        user_id: int,
        skill_name: str,
        skill_slug: str,
    ):
        """Recommendations linked to a gap skill (FK first, then legacy text match)."""
        qs = Recommendation.objects.filter(user_id=user_id).select_related("resource", "target_skill")
        fk_matches = qs.filter(target_skill__slug=skill_slug).order_by("-score")
        if fk_matches.exists():
            return fk_matches
        return qs.filter(
            Q(resource__skills__slug=skill_slug)
            | Q(resource__skills__name__iexact=skill_name)
            | Q(reason__icontains=skill_name)
            | Q(title__icontains=skill_name)
        ).distinct().order_by("-score")

    def get_recommendations_for_roadmap_step(self, user_id: int, step) -> "QuerySet":
        """Recommendations matching any skill tagged on a roadmap step."""
        qs = Recommendation.objects.filter(user_id=user_id).select_related("resource")
        skills = list(step.skills.all())
        if not skills:
            title = (step.title or "").strip()
            if title:
                return qs.filter(
                    Q(title__icontains=title) | Q(reason__icontains=title)
                ).distinct().order_by("-score")
            return qs.none()

        query = Q()
        for skill in skills:
            name = skill.display_name if hasattr(skill, "display_name") else skill.name
            query |= (
                Q(resource__skills__slug=skill.slug)
                | Q(resource__skills__name__iexact=skill.name)
                | Q(reason__icontains=name)
                | Q(title__icontains=name)
            )
        return qs.filter(query).distinct().order_by("-score")
