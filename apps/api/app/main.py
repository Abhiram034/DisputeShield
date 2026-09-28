from uuid import UUID
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from .cases import check_case_policy as evaluate_case_policy, router as cases_router
from .incidents import router as incidents_router
from .merchants import router as merchants_router
from .domain.policy_engine import PolicyAction
from .domain.risk_engine import RiskAssessment, RiskAssessmentInput, assess_risk

app = FastAPI(title="DisputeShield API", version="0.1.0")
app.include_router(cases_router, prefix="/api/cases")
app.include_router(cases_router, prefix="/v1/cases", include_in_schema=False)
app.include_router(incidents_router)
app.include_router(merchants_router)

class PolicyCheckRequest(BaseModel):
    case_id: UUID
    merchant_id: UUID
    action: PolicyAction

@app.get("/health")
def health(): return {"status": "ok", "mode": "deterministic-demo"}

@app.post("/v1/risk/assess", response_model=RiskAssessment)
def risk_assess(req: RiskAssessmentInput):
    """Return an explainable score; missing/failed fields remain explicit UNKNOWNs."""
    return assess_risk(req)

@app.post("/v1/policy/check")
def check_policy(req: PolicyCheckRequest):
    """Read authoritative policy and case facts; callers cannot supply safety flags."""
    return evaluate_case_policy(req.case_id, req)

@app.post("/v1/actions/{transaction_id}/execute")
def execute_legacy(transaction_id: str):
    raise HTTPException(410, "Use the case-scoped idempotent recommendation, approval, and simulated execution endpoints")
