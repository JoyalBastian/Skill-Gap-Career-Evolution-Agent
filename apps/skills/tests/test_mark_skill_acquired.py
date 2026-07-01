from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from apps.careers.models import CareerDomain, CareerPrediction, SkillGapReport
from apps.questionnaire.models import QuestionnaireSession
from apps.skills.models import Skill, UserSkill
from services.progress_service import ProgressService
from services.skill_gap_service import find_gap_info_for_skill

User = get_user_model()


class MarkSkillAcquiredServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="acquire_user", password="test-pass-123")
        self.skill = Skill.objects.create(name="Python", slug="python")
        self.progress = ProgressService()

    def test_mark_skill_acquired_creates_user_skill_and_progress(self):
        entry = self.progress.mark_skill_acquired(
            self.user.id,
            self.skill.id,
            target_proficiency=4,
        )
        self.assertTrue(entry.is_completed)
        us = UserSkill.objects.get(user=self.user, skill=self.skill)
        self.assertEqual(us.proficiency, 4)
        self.assertEqual(us.source, "manual")
        self.assertTrue(self.progress.is_skill_acquired(self.user.id, self.skill.id))

    def test_proficiency_only_upgrades(self):
        UserSkill.objects.create(
            user=self.user,
            skill=self.skill,
            proficiency=5,
            source="resume",
            confidence=0.8,
        )
        self.progress.mark_skill_acquired(
            self.user.id,
            self.skill.id,
            target_proficiency=3,
        )
        us = UserSkill.objects.get(user=self.user, skill=self.skill)
        self.assertEqual(us.proficiency, 5)

    def test_skills_percent_capped_at_100(self):
        for i in range(3):
            sk = Skill.objects.create(name=f"Skill{i}", slug=f"skill-{i}")
            UserSkill.objects.create(user=self.user, skill=sk, proficiency=3)
            self.progress.mark_skill_acquired(self.user.id, sk.id, target_proficiency=5)
        progress = self.progress.get_overall_progress(self.user.id)
        self.assertLessEqual(progress["skills_percent"], 100.0)


class FindGapInfoForSkillTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="gap_user", password="test-pass-123")
        self.career = CareerDomain.objects.create(
            name="Data Analyst",
            slug="data-analyst",
            description="Analyze data",
        )
        self.skill = Skill.objects.create(name="SQL", slug="sql")
        self.gap_report = SkillGapReport.objects.create(
            user=self.user,
            career=self.career,
            employability_score=55.0,
            missing_skills=[
                {
                    "skill_name": "SQL",
                    "slug": "sql",
                    "importance": 4,
                    "user_proficiency": 1,
                    "required_proficiency": 4,
                    "recommended_start_level": "beginner",
                    "gap_score": 5.0,
                    "explanation": "Need SQL for analytics.",
                }
            ],
            prioritized_skills=[
                {
                    "skill": "SQL",
                    "slug": "sql",
                    "gap_score": 5.0,
                    "user_proficiency": 1,
                    "required_proficiency": 4,
                }
            ],
        )

    def test_find_gap_info_for_skill_in_report(self):
        info = find_gap_info_for_skill(self.gap_report, self.skill)
        self.assertIsNotNone(info)
        self.assertEqual(info["required_proficiency"], 4)

    def test_find_gap_info_returns_none_for_unrelated_skill(self):
        other = Skill.objects.create(name="Rust", slug="rust")
        self.assertIsNone(find_gap_info_for_skill(self.gap_report, other))


class MarkSkillAcquiredViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="view_user", password="test-pass-123")
        self.career = CareerDomain.objects.create(
            name="Software Engineer",
            slug="software-engineer",
            description="Build software",
        )
        self.skill = Skill.objects.create(name="Git", slug="git")
        CareerPrediction.objects.create(
            user=self.user,
            career=self.career,
            confidence_pct=80.0,
            rank=1,
        )
        QuestionnaireSession.objects.create(user=self.user, status="completed")
        SkillGapReport.objects.create(
            user=self.user,
            career=self.career,
            employability_score=60.0,
            missing_skills=[
                {
                    "skill_name": "Git",
                    "slug": "git",
                    "importance": 3,
                    "user_proficiency": 0,
                    "required_proficiency": 4,
                    "gap_score": 4.0,
                }
            ],
            prioritized_skills=[
                {
                    "skill": "Git",
                    "slug": "git",
                    "gap_score": 4.0,
                    "user_proficiency": 0,
                    "required_proficiency": 4,
                }
            ],
        )
        self.client.login(username="view_user", password="test-pass-123")

    def test_acquire_post_marks_skill(self):
        url = reverse("skills:gap_skill_acquire", kwargs={"slug": "git"})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ProgressService().is_skill_acquired(self.user.id, self.skill.id))
        us = UserSkill.objects.get(user=self.user, skill=self.skill)
        self.assertEqual(us.proficiency, 4)

    def test_double_acquire_is_idempotent(self):
        url = reverse("skills:gap_skill_acquire", kwargs={"slug": "git"})
        self.client.post(url)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            UserSkill.objects.filter(user=self.user, skill=self.skill).count(),
            1,
        )

    def test_acquire_non_gap_skill_rejected(self):
        Skill.objects.create(name="Docker", slug="docker")
        url = reverse("skills:gap_skill_acquire", kwargs={"slug": "docker"})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        docker = Skill.objects.get(slug="docker")
        self.assertFalse(ProgressService().is_skill_acquired(self.user.id, docker.id))
