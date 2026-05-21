"""Analytics dashboard aggregation service."""
from __future__ import annotations

from apps.analytics.models import AIInsight
from apps.careers.models import CareerPrediction, SkillGapReport
from apps.skills.models import UserSkill
from services.dto import AnalyticsDTO
from services.progress_service import ProgressService


class AnalyticsService:
    def get_dashboard_data(self, user_id: int) -> AnalyticsDTO:
        predictions = CareerPrediction.objects.filter(user_id=user_id).order_by("rank")
        career_data = [
            {
                "name": p.career.name,
                "confidence": p.confidence_pct,
                "rank": p.rank,
            }
            for p in predictions[:5]
        ]

        user_skills = UserSkill.objects.filter(user_id=user_id).select_related("skill")
        skill_heatmap = [
            {
                "name": us.skill.name,
                "proficiency": us.proficiency,
                "confidence": round(us.confidence * 100, 1),
                "category": us.skill.category,
            }
            for us in user_skills
        ]

        # Pull persona traits/interests from Profile.resume_context if present.
        from apps.users.models import Profile

        profile = Profile.objects.filter(user_id=user_id).first()
        persona = ((profile.resume_context or {}).get("persona") if profile else {}) or {}
        personality_data = [
            {"name": t, "score": 80.0}  # Gemini personas don't quantify; show all equally
            for t in (persona.get("traits") or [])[:8]
        ]
        interest_data = [
            {"name": i, "score": 80.0}
            for i in (persona.get("interests") or [])[:8]
        ]

        gap_report = SkillGapReport.objects.filter(user_id=user_id).first()
        employability = gap_report.employability_score if gap_report else 0.0

        progress = ProgressService().get_overall_progress(user_id)

        if employability == 0 and skill_heatmap:
            employability = min(100, len(skill_heatmap) * 12)

        AIInsight.objects.filter(user_id=user_id, insight_type="employability").delete()
        AIInsight.objects.create(
            user_id=user_id,
            insight_type="employability",
            payload={"score": employability, "progress": progress},
        )

        return AnalyticsDTO(
            employability_score=employability,
            career_predictions=career_data,
            personality_traits=personality_data,
            interests=interest_data,
            skill_heatmap=skill_heatmap,
            progress_percent=progress.get("overall", 0),
            learning_percent=progress.get("roadmap_percent", 0),
        )

    def get_chart_json(self, user_id: int) -> dict:
        data = self.get_dashboard_data(user_id)
        return {
            "career_labels": [p["name"] for p in data.career_predictions],
            "career_scores": [p["confidence"] for p in data.career_predictions],
            "personality_labels": [t["name"] for t in data.personality_traits],
            "personality_scores": [t["score"] for t in data.personality_traits],
            "interest_labels": [i["name"] for i in data.interests],
            "interest_scores": [i["score"] for i in data.interests],
            "skill_labels": [s["name"] for s in data.skill_heatmap],
            "skill_scores": [s["proficiency"] for s in data.skill_heatmap],
            "employability": data.employability_score,
            "progress": data.progress_percent,
        }
