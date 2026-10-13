"""Rule engine: loads JSON policies and validates meal plans against them."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

POLICY_DIR = Path(__file__).parent / "policies"

NUTRIENTS = (
    "kcal",
    "carbs_g",
    "fiber_g",
    "added_sugar_g",
    "sodium_mg",
    "sat_fat_g",
    "trans_fat_g",
    "protein_g",
    "fat_g",
)
DERIVED_METRICS = (
    "pct_kcal_added_sugar",
    "pct_kcal_sat_fat",
    "pct_kcal_trans_fat",
    "fiber_per_1000kcal",
)
DAY_ONLY_METRICS = ("meal_count",)
PLAN_ONLY_METRICS = ("carb_day_spread_g",)

VALID_SCOPES = {"item", "meal", "day", "plan"}
VALID_SEVERITIES = {"error", "warning"}
VALID_OPS = {"<=", ">=", "between"}
VALID_RULE_TYPES = {"limit", "forbidden_tag"}
VALID_MEAL_TYPES = {"breakfast", "lunch", "dinner", "snack"}
SEVERITY_WEIGHT = {"error": 3, "warning": 1}


class PolicyError(ValueError):
    """Raised when a policy file is malformed."""


# ---------------------------------------------------------------- policies
def _metrics_for_scope(scope: str) -> set[str]:
    base = set(NUTRIENTS) | set(DERIVED_METRICS)
    if scope == "day":
        return base | set(DAY_ONLY_METRICS)
    if scope == "plan":
        return set(PLAN_ONLY_METRICS)
    return base


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    for key in ("id", "name", "rules"):
        if key not in policy:
            raise PolicyError(f"policy missing required field '{key}'")
    seen: set[str] = set()
    for rule in policy["rules"]:
        rid = rule.get("id")
        if not rid:
            raise PolicyError(f"{policy['id']}: rule without id")
        if rid in seen:
            raise PolicyError(f"{policy['id']}: duplicate rule id {rid}")
        seen.add(rid)
        for key in ("title", "type", "scope", "severity"):
            if key not in rule:
                raise PolicyError(f"{rid}: missing '{key}'")
        if rule["type"] not in VALID_RULE_TYPES:
            raise PolicyError(f"{rid}: invalid type {rule['type']}")
        if rule["scope"] not in VALID_SCOPES:
            raise PolicyError(f"{rid}: invalid scope {rule['scope']}")
        if rule["severity"] not in VALID_SEVERITIES:
            raise PolicyError(f"{rid}: invalid severity {rule['severity']}")
        bad_meals = set(rule.get("applies_to", [])) - VALID_MEAL_TYPES
        if bad_meals:
            raise PolicyError(f"{rid}: unknown meal types {sorted(bad_meals)}")
        if rule["type"] == "forbidden_tag":
            if rule["scope"] != "item":
                raise PolicyError(f"{rid}: forbidden_tag rules must use scope 'item'")
            if not rule.get("tags"):
                raise PolicyError(f"{rid}: forbidden_tag rule needs 'tags'")
        else:
            if rule["scope"] == "item":
                raise PolicyError(f"{rid}: limit rules cannot use scope 'item'")
            if rule.get("metric") not in _metrics_for_scope(rule["scope"]):
                raise PolicyError(
                    f"{rid}: metric '{rule.get('metric')}' not valid for scope '{rule['scope']}'"
                )
            op = rule.get("op")
            if op not in VALID_OPS:
                raise PolicyError(f"{rid}: invalid op {op}")
            if op == "between":
                if "min" not in rule or "max" not in rule or rule["min"] > rule["max"]:
                    raise PolicyError(f"{rid}: 'between' needs min <= max")
            elif "value" not in rule:
                raise PolicyError(f"{rid}: missing 'value'")
    return policy


def load_policy(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return validate_policy(json.load(fh))


def load_policies(directory: Path = POLICY_DIR) -> dict[str, dict[str, Any]]:
    policies: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(directory).glob("*.json")):
        policy = load_policy(path)
        if policy["id"] in policies:
            raise PolicyError(f"duplicate policy id {policy['id']}")
        policies[policy["id"]] = policy
    return policies


# ----------------------------------------------------------------- metrics
def _sum_items(items: list[dict[str, Any]]) -> dict[str, float]:
    totals = {n: 0.0 for n in NUTRIENTS}
    for item in items:
        servings = item.get("servings", 1) or 1
        for n in NUTRIENTS:
            totals[n] += float(item.get(n, 0) or 0) * servings
    return totals


def _derive(totals: dict[str, float]) -> dict[str, float | None]:
    metrics: dict[str, float | None] = dict(totals)
    kcal = totals["kcal"]
    if kcal > 0:
        metrics["pct_kcal_added_sugar"] = totals["added_sugar_g"] * 4 / kcal * 100
        metrics["pct_kcal_sat_fat"] = totals["sat_fat_g"] * 9 / kcal * 100
        metrics["pct_kcal_trans_fat"] = totals["trans_fat_g"] * 9 / kcal * 100
        metrics["fiber_per_1000kcal"] = totals["fiber_g"] / kcal * 1000
    else:  # ratios are undefined without energy
        for m in DERIVED_METRICS:
            metrics[m] = None
    return metrics


def meal_metrics(meal: dict[str, Any]) -> dict[str, float | None]:
    return _derive(_sum_items(meal["items"]))


def day_metrics(day: dict[str, Any]) -> dict[str, float | None]:
    items = [i for m in day["meals"] for i in m["items"]]
    metrics = _derive(_sum_items(items))
    metrics["meal_count"] = float(len(day["meals"]))
    return metrics


def plan_metrics(plan: dict[str, Any]) -> dict[str, float | None]:
    carbs = [day_metrics(d)["carbs_g"] for d in plan["days"]]
    spread = (max(carbs) - min(carbs)) if len(carbs) >= 2 else None
    return {"carb_day_spread_g": spread}


# ------------------------------------------------------------------- rules
def _check(rule: dict[str, Any], actual: float) -> tuple[bool, str]:
    op = rule["op"]
    if op == "<=":
        return actual <= rule["value"], f"<= {rule['value']}"
    if op == ">=":
        return actual >= rule["value"], f">= {rule['value']}"
    return rule["min"] <= actual <= rule["max"], f"{rule['min']} to {rule['max']}"


def _finding(rule: dict[str, Any], status: str, location: str, **extra: Any) -> dict[str, Any]:
    base = {
        "rule_id": rule["id"],
        "title": rule["title"],
        "severity": rule["severity"],
        "status": status,
        "scope": rule["scope"],
        "location": location,
        "metric": rule.get("metric"),
        "actual": None,
        "limit": None,
        "message": "",
        "remedy": rule.get("remedy") if status == "fail" else None,
        "evidence": rule.get("evidence"),
    }
    base.update(extra)
    return base


def _limit_finding(rule: dict[str, Any], actual: float | None, location: str) -> dict[str, Any]:
    if actual is None:
        return _finding(
            rule, "skipped", location,
            message=f"{rule['metric']} cannot be computed for this scope (no energy or too few days).",
        )
    ok, limit = _check(rule, actual)
    unit = rule["metric"]
    msg = (
        f"{unit} = {actual:.1f} meets requirement ({limit})."
        if ok
        else f"{unit} = {actual:.1f} violates requirement ({limit})."
    )
    return _finding(
        rule, "pass" if ok else "fail", location,
        actual=round(actual, 1), limit=limit, message=msg,
    )


def _applies(rule: dict[str, Any], meal_type: str) -> bool:
    types = rule.get("applies_to")
    return not types or meal_type in types


def _evaluate_rule(rule: dict[str, Any], plan: dict[str, Any], results: list[dict[str, Any]]) -> None:
    scope = rule["scope"]

    if scope == "plan":
        results.append(_limit_finding(rule, plan_metrics(plan).get(rule["metric"]), "Plan"))
        return

    for day in plan["days"]:
        dlabel = day["label"]
        if scope == "day":
            results.append(_limit_finding(rule, day_metrics(day).get(rule["metric"]), dlabel))
        elif scope == "meal":
            for meal in day["meals"]:
                if _applies(rule, meal["type"]):
                    loc = f"{dlabel} / {meal.get('name') or meal['type']}"
                    results.append(_limit_finding(rule, meal_metrics(meal).get(rule["metric"]), loc))
        elif scope == "item":
            tags = {t.lower() for t in rule["tags"]}
            offenders = []
            for meal in day["meals"]:
                if not _applies(rule, meal["type"]):
                    continue
                for item in meal["items"]:
                    hit = tags & {t.lower() for t in item.get("tags", [])}
                    if hit:
                        offenders.append((meal, item, sorted(hit)))
            if not offenders:
                results.append(_finding(rule, "pass", dlabel, message="No prohibited items found."))
            for meal, item, hit in offenders:
                loc = f"{dlabel} / {meal.get('name') or meal['type']}"
                results.append(
                    _finding(
                        rule, "fail", loc,
                        message=f"'{item['name']}' is prohibited by this policy (tag: {', '.join(hit)}).",
                    )
                )


# -------------------------------------------------------------- public API
def validate_plan(policy: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for rule in policy["rules"]:
        _evaluate_rule(rule, plan, results)

    evaluated = [r for r in results if r["status"] != "skipped"]
    failed = [r for r in evaluated if r["status"] == "fail"]
    errors = sum(1 for r in failed if r["severity"] == "error")
    warnings = sum(1 for r in failed if r["severity"] == "warning")

    total_w = sum(SEVERITY_WEIGHT[r["severity"]] for r in evaluated)
    lost_w = sum(SEVERITY_WEIGHT[r["severity"]] for r in failed)
    score = round(100 * (total_w - lost_w) / total_w, 1) if total_w else 100.0

    if errors:
        verdict = "NON_COMPLIANT"
    elif warnings:
        verdict = "COMPLIANT_WITH_WARNINGS"
    else:
        verdict = "COMPLIANT"

    return {
        "policy_id": policy["id"],
        "policy_name": policy["name"],
        "policy_version": policy.get("version"),
        "patient_id": plan.get("patient_id"),
        "verdict": verdict,
        "score": score,
        "summary": {
            "checks": len(evaluated),
            "passed": len(evaluated) - len(failed),
            "errors": errors,
            "warnings": warnings,
            "skipped": len(results) - len(evaluated),
        },
        "findings": results,
        "disclaimer": policy.get("disclaimer"),
    }
