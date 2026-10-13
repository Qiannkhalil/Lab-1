"""FastAPI backend for the Evidence-Based Policy Rule Engine."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from . import engine
from .models import PolicySummary, ValidateRequest, ValidationReport


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.policies = engine.load_policies()
    yield


app = FastAPI(
    title="Evidence-Based Policy Rule Engine",
    description="Validates meal plans against codified dietary policies (low-sodium, diabetic, heart-healthy).",
    version="1.0.0",
    lifespan=lifespan,
)


def _get_policy(policy_id: str) -> dict[str, Any]:
    policy = app.state.policies.get(policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Unknown policy '{policy_id}'")
    return policy


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "policies_loaded": len(app.state.policies)}


@app.get("/policies", response_model=list[PolicySummary])
def list_policies():
    return [
        PolicySummary(
            id=p["id"],
            name=p["name"],
            version=p.get("version"),
            description=p.get("description"),
            rule_count=len(p["rules"]),
        )
        for p in app.state.policies.values()
    ]


@app.get("/policies/{policy_id}")
def get_policy(policy_id: str) -> dict[str, Any]:
    """Return the full codified policy, including rules and evidence citations."""
    return _get_policy(policy_id)


@app.post("/validate", response_model=ValidationReport)
def validate(req: ValidateRequest):
    policy = _get_policy(req.policy_id)
    return engine.validate_plan(policy, req.plan.model_dump(mode="json"))
