import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.api.app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)
def test_health():
    assert client.get("/health").json()["status"] == "ok"

def test_risk_assessment_api_preserves_unknowns_and_separates_opportunity():
    response = client.post("/v1/risk/assess", json={
        "transaction_id": "tx-risk-api",
        "transaction_amount_minor": {"status": "KNOWN", "value": 12000},
        "payment_succeeded": {"status": "KNOWN", "value": True},
        "customer_average_amount_minor": {"status": "KNOWN", "value": 4000},
        "receipt_failed": {"status": "KNOWN", "value": True},
        "dispute_exists": {"status": "KNOWN", "value": False},
        "issue_resolved": {"status": "KNOWN", "value": False},
        "customer_opted_out": {"status": "KNOWN", "value": False},
        "safe_actions_available": {"status": "KNOWN", "value": ["send_payment_confirmation"]},
    })
    assert response.status_code == 200
    body = response.json()
    assert body["risk_level"] == "UNKNOWN"  # history/signal coverage is incomplete
    assert body["intervention_opportunity"] == "HIGH"
    assert any("previous_disputes: unknown" in item for item in body["unknowns"])
