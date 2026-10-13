"""Pydantic request/response models."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MealType(str, Enum):
    breakfast = "breakfast"
    lunch = "lunch"
    dinner = "dinner"
    snack = "snack"


class FoodItem(BaseModel):
    name: str
    servings: float = Field(1, gt=0, description="Multiplier applied to the per-serving nutrients")
    kcal: float = Field(0, ge=0)
    carbs_g: float = Field(0, ge=0)
    fiber_g: float = Field(0, ge=0)
    added_sugar_g: float = Field(0, ge=0)
    sodium_mg: float = Field(0, ge=0)
    sat_fat_g: float = Field(0, ge=0)
    trans_fat_g: float = Field(0, ge=0)
    protein_g: float = Field(0, ge=0)
    fat_g: float = Field(0, ge=0)
    tags: list[str] = Field(default_factory=list)


class Meal(BaseModel):
    name: Optional[str] = None
    type: MealType
    items: list[FoodItem] = Field(default_factory=list)


class DayPlan(BaseModel):
    label: str = "Day 1"
    meals: list[Meal] = Field(min_length=1)


class MealPlan(BaseModel):
    patient_id: Optional[str] = None
    days: list[DayPlan] = Field(min_length=1)


class ValidateRequest(BaseModel):
    policy_id: str
    plan: MealPlan


class Evidence(BaseModel):
    source: str
    note: str


class Finding(BaseModel):
    rule_id: str
    title: str
    severity: str
    status: str
    scope: str
    location: str
    metric: Optional[str] = None
    actual: Optional[float] = None
    limit: Optional[str] = None
    message: str
    remedy: Optional[str] = None
    evidence: Optional[Evidence] = None


class Summary(BaseModel):
    checks: int
    passed: int
    errors: int
    warnings: int
    skipped: int


class ValidationReport(BaseModel):
    policy_id: str
    policy_name: str
    policy_version: Optional[str] = None
    patient_id: Optional[str] = None
    verdict: str
    score: float
    summary: Summary
    findings: list[Finding]
    disclaimer: Optional[str] = None


class PolicySummary(BaseModel):
    id: str
    name: str
    version: Optional[str] = None
    description: Optional[str] = None
    rule_count: int
