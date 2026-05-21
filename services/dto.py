"""Data transfer objects for the service layer."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CareerPredictionDTO:
    career_slug: str
    career_name: str
    confidence_pct: float
    rank: int
    explanation: str


@dataclass
class SkillGapDTO:
    skill_name: str
    skill_slug: str
    importance: int
    user_proficiency: float
    required_proficiency: int
    gap_score: float
    is_missing: bool


@dataclass
class RecommendationDTO:
    title: str
    resource_type: str
    url: str
    score: float
    reason: str
    skill_names: list[str] = field(default_factory=list)


@dataclass
class ResumeAnalysisDTO:
    skills_detected: list[dict[str, Any]]
    experience_years: float
    education: list[dict[str, Any]]
    employability_score: float
    summary: str


@dataclass
class AnalyticsDTO:
    employability_score: float
    career_predictions: list[dict[str, Any]]
    personality_traits: list[dict[str, Any]]
    interests: list[dict[str, Any]]
    skill_heatmap: list[dict[str, Any]]
    progress_percent: float
    learning_percent: float
