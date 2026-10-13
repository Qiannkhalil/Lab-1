"""Command-line validator.

Usage:
    python -m app.cli sample_plans/diabetic_compliant.json --policy diabetic_consistent_carb
"""
from __future__ import annotations

import argparse
import json
import sys

from . import engine
from .models import MealPlan

ICON = {"pass": "PASS", "fail": "FAIL", "skipped": "SKIP"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a meal plan against a dietary policy")
    parser.add_argument("plan", help="Path to meal plan JSON")
    parser.add_argument("--policy", required=True, help="Policy id (see app/policies)")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON")
    args = parser.parse_args(argv)

    policies = engine.load_policies()
    if args.policy not in policies:
        print(f"Unknown policy '{args.policy}'. Available: {', '.join(policies)}", file=sys.stderr)
        return 2

    with open(args.plan, encoding="utf-8") as fh:
        plan = MealPlan.model_validate(json.load(fh)).model_dump(mode="json")

    report = engine.validate_plan(policies[args.policy], plan)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        s = report["summary"]
        print(f"{report['policy_name']}  (v{report['policy_version']})")
        print(f"Verdict: {report['verdict']}   Score: {report['score']}")
        print(f"Checks: {s['checks']}  passed: {s['passed']}  errors: {s['errors']}  warnings: {s['warnings']}")
        for f in report["findings"]:
            if f["status"] == "fail":
                print(f"  [{ICON[f['status']]}] {f['rule_id']} ({f['severity']}) @ {f['location']}: {f['message']}")
                if f["remedy"]:
                    print(f"         fix: {f['remedy']}")
    return 0 if report["verdict"] != "NON_COMPLIANT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
