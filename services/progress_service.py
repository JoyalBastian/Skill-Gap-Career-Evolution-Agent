"""Progress tracking service."""
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

from apps.progress.models import ProgressEntry
from apps.roadmap.models import RoadmapStep
from apps.skills.models import Skill, UserSkill


class ProgressService:
    def mark_step_complete(self, user_id: int, step_id: int) -> ProgressEntry:
        ct = ContentType.objects.get_for_model(RoadmapStep)
        entry, _ = ProgressEntry.objects.update_or_create(
            user_id=user_id,
            content_type=ct,
            object_id=step_id,
            defaults={
                "is_completed": True,
                "percent_complete": 100.0,
                "completed_at": timezone.now(),
            },
        )
        return entry

    def mark_skill_complete(self, user_id: int, skill_id: int) -> ProgressEntry:
        ct = ContentType.objects.get_for_model(Skill)
        entry, _ = ProgressEntry.objects.update_or_create(
            user_id=user_id,
            content_type=ct,
            object_id=skill_id,
            defaults={
                "is_completed": True,
                "percent_complete": 100.0,
                "completed_at": timezone.now(),
            },
        )
        UserSkill.objects.filter(user_id=user_id, skill_id=skill_id).update(proficiency=5)
        return entry

    def get_overall_progress(self, user_id: int) -> dict:
        from apps.roadmap.models import Roadmap

        roadmap = Roadmap.objects.filter(user_id=user_id, is_active=True).first()
        if not roadmap:
            from apps.recommendations.models import Recommendation
            rec_total = Recommendation.objects.filter(user_id=user_id).count()
            learning_pct = min(100.0, rec_total * 12.5) if rec_total else 0.0
            return {
                "roadmap_percent": 0,
                "skills_percent": 0,
                "learning_percent": round(learning_pct, 1),
                "overall": round(learning_pct / 3, 1),
                "recommendations_count": rec_total,
            }

        total_steps = roadmap.steps.count()
        completed_steps = ProgressEntry.objects.filter(
            user_id=user_id,
            content_type=ContentType.objects.get_for_model(RoadmapStep),
            is_completed=True,
            object_id__in=roadmap.steps.values_list("id", flat=True),
        ).count()

        total_skills = UserSkill.objects.filter(user_id=user_id).count()
        completed_skills = ProgressEntry.objects.filter(
            user_id=user_id,
            content_type=ContentType.objects.get_for_model(Skill),
            is_completed=True,
        ).count()

        from apps.recommendations.models import Recommendation

        roadmap_pct = (completed_steps / total_steps * 100) if total_steps else 0
        skills_pct = (completed_skills / total_skills * 100) if total_skills else 0
        rec_total = Recommendation.objects.filter(user_id=user_id).count()
        learning_pct = min(100.0, rec_total * 12.5) if rec_total else 0.0

        return {
            "roadmap_percent": round(roadmap_pct, 1),
            "skills_percent": round(skills_pct, 1),
            "learning_percent": round(learning_pct, 1),
            "overall": round((roadmap_pct + skills_pct + learning_pct) / 3, 1),
            "completed_steps": completed_steps,
            "total_steps": total_steps,
            "recommendations_count": rec_total,
        }
