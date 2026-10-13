"""Tests for the rule engine and API."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import engine
from app.main import app
from app.models import MealPlan

ROOT = Path(__file__).resolve().parent.parent
POLICIES = engine.load_policies()


def load_plan(name):
    data = json.loads((ROOT / "sample_plans" / name).read_text())
    return MealPlan.model_validate(data).model_dump(mode="json")


def failed_ids(report):
    return {f["rule_id"] for f in report["findings"] if f["status"] == "fail"}


# ---------------------------------------------------------------- policies
def test_all_bundled_policies_load_and_validate():
    assert {"low_sodium", "diabetic_consistent_carb", "heart_healthy"} <= set(POLICIES)


def test_every_rule_has_evidence():
    for policy in POLICIES.values():
        for rule in policy["rules"]:
            assert rule.get("evidence", {}).get("source"), f"{rule['id']} missing evidence"


def test_malformed_policy_rejected():
    bad = {"id": "x", "name": "x", "rules": [{"id": "R1", "title": "t", "type": "limit",
                                                 "scope": "day", "severity": "error",
                                                 "metric": "nonsense", "op": "<=", "value": 1}]}
    with pytest.raises(engine.PolicyError):
        engine.validate_policy(bad)


def test_duplicate_rule_ids_rejected():
    rule = {"id": "R1", "title": "t", "type": "limit", "scope": "day", "severity": "error",
            "metric": "kcal", "op": "<=", "value": 1}
    with pytest.raises(engine.PolicyError):
        engine.validate_policy({"id": "x", "name": "x", "rules": [rule, dict(rule)]})


# ------------------------------------------------------------------ engine
def test_compliant_diabetic_plan():
    report = engine.validate_plan(POLICIES["diabetic_consistent_carb"], load_plan("diabetic_compliant.json"))
    assert report["verdict"] == "COMPLIANT", failed_ids(report)
    assert report["score"] == 100.0


def test_noncompliant_diabetic_plan_flags_expected_rules():
    report = engine.validate_plan(POLICIES["diabetic_consistent_carb"], load_plan("diabetic_noncompliant.json"))
    assert report["verdict"] == "NON_COMPLIANT"
    assert {"DM-01", "DM-03", "DM-04", "DM-06", "DM-07"} <= failed_ids(report)
    assert "DM-08" not in failed_ids(report)  # three meals present, so frequency rule passes
    assert report["score"] < 100


def test_low_sodium_flags_daily_and_forbidden_items():
    report = engine.validate_plan(POLICIES["low_sodium"], load_plan("diabetic_noncompliant.json"))
    assert {"LS-01", "LS-02", "LS-04"} <= failed_ids(report)


def test_sodium_boundary_is_inclusive():
    plan = {"days": [{"label": "D", "meals": [
        {"type": "breakfast", "items": [{"name": "a", "kcal": 500, "sodium_mg": 500}]},
        {"type": "lunch", "items": [{"name": "b", "kcal": 500, "sodium_mg": 700}]},
        {"type": "dinner", "items": [{"name": "c", "kcal": 500, "sodium_mg": 700}]},
        {"type": "snack", "items": [{"name": "d", "kcal": 100, "sodium_mg": 100}]},
    ]}]}
    report = engine.validate_plan(POLICIES["low_sodium"], plan)
    assert report["verdict"] == "COMPLIANT", failed_ids(report)  # exactly 2000 mg
    plan["days"][0]["meals"][3]["items"][0]["sodium_mg"] = 101
    assert "LS-01" in failed_ids(engine.validate_plan(POLICIES["low_sodium"], plan))


def test_servings_multiplier():
    plan = {"days": [{"label": "D", "meals": [
        {"type": "lunch", "items": [{"name": "x", "servings": 3, "kcal": 100, "carbs_g": 20}]}]}]}
    day = plan["days"][0]
    assert engine.day_metrics(day)["carbs_g"] == 60


def test_warning_only_gives_compliant_with_warnings():
    plan = load_plan("diabetic_compliant.json")
    plan["days"][0]["meals"][2]["items"] = [  # snack with only 3 g carbs -> DM-02 warning
        {"name": "Almonds", "kcal": 80, "carbs_g": 3, "fiber_g": 1.7, "sat_fat_g": 0.5}]
    report = engine.validate_plan(POLICIES["diabetic_consistent_carb"], plan)
    assert report["verdict"] == "COMPLIANT_WITH_WARNINGS"
    assert "DM-02" in failed_ids(report)


def test_ratio_metrics_skipped_without_energy():
    plan = {"days": [{"label": "D", "meals": [{"type": "breakfast", "items": []}]}]}
    report = engine.validate_plan(POLICIES["heart_healthy"], plan)
    assert report["summary"]["skipped"] > 0


def test_plan_scope_consistency_rule():
    day1 = load_plan("diabetic_compliant.json")["days"][0]
    day2 = copy.deepcopy(day1)
    day2["label"] = "Day 2"
    for meal in day2["meals"]:
        for item in meal["items"]:
            item["carbs_g"] = item.get("carbs_g", 0) * 0.5
    plan = {"days": [day1, day2]}
    assert "DM-09" in failed_ids(engine.validate_plan(POLICIES["diabetic_consistent_carb"], plan))


# --------------------------------------------------------------------- API
@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_api_health_and_policy_listing(client):
    assert client.get("/health").json()["status"] == "ok"
    ids = {p["id"] for p in client.get("/policies").json()}
    assert "low_sodium" in ids
    assert client.get("/policies/low_sodium").json()["rules"]


def test_api_unknown_policy_404(client):
    assert client.get("/policies/nope").status_code == 404
    body = {"policy_id": "nope", "plan": {"days": [{"label": "D", "meals": [{"type": "lunch", "items": []}]}]}}
    assert client.post("/validate", json=body).status_code == 404


def test_api_validate_roundtrip(client):
    plan = json.loads((ROOT / "sample_plans" / "diabetic_noncompliant.json").read_text())
    r = client.post("/validate", json={"policy_id": "diabetic_consistent_carb", "plan": plan})
    assert r.status_code == 200
    assert r.json()["verdict"] == "NON_COMPLIANT"


def test_api_rejects_invalid_input(client):
    body = {"policy_id": "low_sodium", "plan": {"days": [{"label": "D", "meals": [
        {"type": "brunch", "items": []}]}]}}
    assert client.post("/validate", json=body).status_code == 422
