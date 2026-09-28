"""PostgreSQL-backed API tests. Set TEST_DATABASE_URL to an isolated test database."""
import os
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from apps.api.app.main import app


@pytest.fixture
def synthetic_records(monkeypatch):
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL case integration tests")
    monkeypatch.setenv("DATABASE_URL", dsn)
    with psycopg.connect(dsn) as conn:
        if not conn.info.dbname.endswith("_test"):
            pytest.fail("Refusing integration test writes unless TEST_DATABASE_URL targets a *_test database")
    merchant_id, customer_id, transaction_id, order_id = (uuid4() for _ in range(4))
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO merchants(id,name) VALUES (%s,%s)", (merchant_id, "Integration Fixture"))
        conn.execute("""
            INSERT INTO merchant_policies(merchant_id,version,auto_send_receipt,auto_create_support_case,
                communication_enabled,auto_refund_limit,high_value_threshold)
            VALUES (%s,1,true,true,true,0,50000)
        """, (merchant_id,))
        conn.execute("""
            INSERT INTO customers(id,merchant_id,synthetic_external_id,average_transaction_amount,
                transaction_count,previous_disputes,previous_refunds,support_contact_count)
            VALUES (%s,%s,%s,5000,3,0,0,1)
        """, (customer_id, merchant_id, f"test-{customer_id.hex[:8]}"))
        conn.execute("""
            INSERT INTO transactions(id,merchant_id,customer_id,order_id,amount,currency,payment_method,
                merchant_descriptor,status,created_at)
            VALUES (%s,%s,%s,%s,20000,'USD','card','TEST*SHOP','succeeded',now())
        """, (transaction_id, merchant_id, customer_id, order_id))
        conn.execute("""
            INSERT INTO orders(id,merchant_id,customer_id,transaction_id,order_amount,order_status,
                delivery_status,promised_delivery_at,created_at)
            VALUES (%s,%s,%s,%s,20000,'paid','failed',now()+interval '1 day',now())
        """, (order_id, merchant_id, customer_id, transaction_id))
        conn.execute("""
            INSERT INTO post_payment_events(merchant_id,transaction_id,customer_id,event_type,metadata,created_at)
            VALUES (%s,%s,%s,'delivery_failed',%s,now())
        """, (merchant_id, transaction_id, customer_id, Jsonb({"synthetic": True})))
    yield merchant_id, customer_id, transaction_id
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM outcomes WHERE transaction_id=%s", (transaction_id,))
        conn.execute("""DELETE FROM audit_logs WHERE entity_type='intervention' AND entity_id IN
                      (SELECT id FROM interventions WHERE transaction_id=%s)""", (transaction_id,))
        conn.execute("DELETE FROM interventions WHERE transaction_id=%s", (transaction_id,))
        conn.execute("DELETE FROM investigations WHERE transaction_id=%s", (transaction_id,))
        conn.execute("""
            DELETE FROM audit_logs
            WHERE (entity_type='case' AND entity_id IN (SELECT id FROM cases WHERE transaction_id=%s))
               OR (entity_type='risk_assessment' AND entity_id IN
                   (SELECT id FROM risk_assessments WHERE transaction_id=%s))
        """, (transaction_id, transaction_id))
        conn.execute("DELETE FROM cases WHERE transaction_id=%s", (transaction_id,))
        conn.execute("DELETE FROM risk_assessments WHERE transaction_id=%s", (transaction_id,))
        conn.execute("DELETE FROM post_payment_events WHERE transaction_id=%s", (transaction_id,))
        conn.execute("DELETE FROM disputes WHERE transaction_id=%s", (transaction_id,))
        conn.execute("DELETE FROM merchant_incidents WHERE merchant_id=%s", (merchant_id,))
        conn.execute("DELETE FROM orders WHERE transaction_id=%s", (transaction_id,))
        conn.execute("DELETE FROM transactions WHERE id=%s", (transaction_id,))
        conn.execute("DELETE FROM customers WHERE id=%s", (customer_id,))
        conn.execute("DELETE FROM merchant_policies WHERE merchant_id=%s", (merchant_id,))
        conn.execute("DELETE FROM merchants WHERE id=%s", (merchant_id,))


def risk_payload(transaction_id):
    known = lambda value: {"status": "KNOWN", "value": value, "source": "integration_fixture"}
    return {
        "transaction_id": str(transaction_id),
        "transaction_amount_minor": known(20000),
        "payment_succeeded": known(True),
        "customer_average_amount_minor": known(5000),
        "previous_disputes": known(0),
        "delivery_failed": known(True),
        "dispute_exists": known(False),
        "issue_resolved": known(False),
        "customer_opted_out": known(False),
        "safe_actions_available": known(["create_support_case"]),
    }


def test_case_detection_listing_detail_and_read_only_tool_snapshot(synthetic_records):
    merchant_id, _, transaction_id = synthetic_records
    client = TestClient(app)
    payload = {"merchant_id": str(merchant_id), "assessment": risk_payload(transaction_id)}
    detected = client.post("/api/cases/detect", json=payload)
    assert detected.status_code == 200, detected.text
    first = detected.json()
    assert first["opened"] is True and first["state"] == "DETECTED"

    repeated = client.post("/api/cases/detect", json=payload)
    assert repeated.status_code == 200 and repeated.json()["case_id"] == first["case_id"]

    listing = client.get("/api/cases", params={"merchant_id": str(merchant_id)})
    assert listing.status_code == 200 and len(listing.json()["items"]) == 1
    detail = client.get(f"/api/cases/{first['case_id']}", params={"merchant_id": str(merchant_id)})
    assert detail.status_code == 200 and detail.json()["risk_level"] == "MEDIUM"
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        before = conn.execute("""
            SELECT (SELECT count(*) FROM investigations), (SELECT count(*) FROM interventions),
                   (SELECT count(*) FROM audit_logs)
        """).fetchone()

    snapshot = client.get(f"/api/cases/{first['case_id']}/investigation-tools", params={"merchant_id": str(merchant_id)})
    assert snapshot.status_code == 200
    tools = snapshot.json()
    assert tools["transaction"]["status"] == "KNOWN"
    assert tools["order_status"]["status"] == "KNOWN"
    assert tools["receipt_status"]["status"] == "EMPTY"
    assert tools["merchant_policy"]["status"] == "KNOWN"
    assert tools["similar_cases"]["status"] == "EMPTY"
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        after = conn.execute("""
            SELECT (SELECT count(*) FROM investigations), (SELECT count(*) FROM interventions),
                   (SELECT count(*) FROM audit_logs)
        """).fetchone()
    assert before == after  # investigation snapshots are read-only


def test_case_reads_are_merchant_scoped(synthetic_records):
    merchant_id, _, transaction_id = synthetic_records
    client = TestClient(app)
    payload = {"merchant_id": str(merchant_id), "assessment": risk_payload(transaction_id)}
    case = client.post("/api/cases/detect", json=payload).json()
    other_merchant = str(uuid4())
    response = client.get(f"/api/cases/{case['case_id']}", params={"merchant_id": other_merchant})
    assert response.status_code == 404


def test_ai_disabled_investigation_is_recorded_and_escalated(synthetic_records, monkeypatch):
    merchant_id, _, transaction_id = synthetic_records
    monkeypatch.setenv("LLM_PROVIDER", "none")
    client = TestClient(app)
    case = client.post("/api/cases/detect", json={
        "merchant_id": str(merchant_id), "assessment": risk_payload(transaction_id),
    }).json()
    response = client.post(f"/api/cases/{case['case_id']}/investigate", params={"merchant_id": str(merchant_id)})
    assert response.status_code == 200
    assert response.json()["status"] == "UNAVAILABLE"
    assert response.json()["route"] == "human_review"
    detail = client.get(f"/api/cases/{case['case_id']}", params={"merchant_id": str(merchant_id)})
    assert detail.json()["state"] == "ESCALATED"
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        saved = conn.execute("SELECT status FROM investigations WHERE transaction_id=%s", (transaction_id,)).fetchone()
    assert saved[0] == "UNAVAILABLE"


def test_investigation_evidence_is_persisted_and_merchant_scoped(synthetic_records, monkeypatch):
    merchant_id, _, transaction_id = synthetic_records
    client = TestClient(app)
    case = client.post("/api/cases/detect", json={
        "merchant_id": str(merchant_id), "assessment": risk_payload(transaction_id),
    }).json()

    def completed_report(*args, **kwargs):
        return "COMPLETED", {
            "risk_level": "MEDIUM", "primary_hypothesis": "Delivery failure may explain the dispute",
            "supporting_evidence": [], "contradicting_evidence": [],
            "unknowns": ["Carrier scan unavailable"], "recommended_action": "flag_human_review",
            "confidence": 0.6,
            "tool_results": [{"tool_name": "get_transaction", "status": "KNOWN", "error_code": None,
                              "data": {"id": transaction_id, "status": "succeeded"}}],
        }

    monkeypatch.setattr("apps.api.app.cases.investigate_case", completed_report)
    response = client.post(f"/api/cases/{case['case_id']}/investigate", params={"merchant_id": str(merchant_id)})
    assert response.status_code == 200, response.text
    evidence = client.get(f"/api/cases/{case['case_id']}/evidence", params={"merchant_id": str(merchant_id)})
    assert evidence.status_code == 200
    item = next(row for row in evidence.json()["items"] if row["field_name"] == "status")
    assert item["evidence_ref"] == f"get_transaction:{transaction_id}:status"
    assert item["value"] == "succeeded"
    assert item["data_status"] == "KNOWN"
    policy = client.post(f"/api/cases/{case['case_id']}/policy-check", json={
        "merchant_id": str(merchant_id), "action": "send_delivery_update",
        "risk_level": "LOW", "communication_opt_out": False,
    })
    assert policy.status_code == 200, policy.text
    assert policy.json()["decision"] == "RECOMMENDATION_ELIGIBLE"
    assert policy.json()["policy_version"] == 1
    assert policy.json()["reason_code"] == "POLICY_PASSED"
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        conn.execute("UPDATE customers SET communication_opt_out=true WHERE id=%s", (synthetic_records[1],))
    opted_out = client.post(f"/v1/policy/check", json={
        "case_id": case["case_id"], "merchant_id": str(merchant_id), "action": "send_delivery_update",
    })
    assert opted_out.status_code == 200
    assert opted_out.json()["decision"] == "BLOCKED"
    assert opted_out.json()["reason_code"] == "COMMUNICATION_BLOCKED"
    hidden = client.get(f"/api/cases/{case['case_id']}/evidence", params={"merchant_id": str(uuid4())})
    assert hidden.status_code == 404


def _prepare_case_with_evidence(synthetic_records, monkeypatch):
    merchant_id, _, transaction_id = synthetic_records
    client = TestClient(app)
    case = client.post("/api/cases/detect", json={
        "merchant_id": str(merchant_id), "assessment": risk_payload(transaction_id),
    }).json()

    def completed_report(*args, **kwargs):
        return "COMPLETED", {
            "risk_level": "MEDIUM", "primary_hypothesis": "Delivery failure may explain the dispute",
            "supporting_evidence": [], "contradicting_evidence": [], "unknowns": [],
            "recommended_action": "send_delivery_update", "confidence": 0.8,
            "tool_results": [{"tool_name": "get_transaction", "status": "KNOWN", "error_code": None,
                              "data": {"id": transaction_id, "status": "succeeded"}}],
        }

    monkeypatch.setattr("apps.api.app.cases.investigate_case", completed_report)
    investigated = client.post(f"/api/cases/{case['case_id']}/investigate", params={"merchant_id": str(merchant_id)})
    assert investigated.status_code == 200
    return client, merchant_id, case["case_id"]


def test_action_recommendation_and_simulation_are_idempotent(synthetic_records, monkeypatch):
    client, merchant_id, case_id = _prepare_case_with_evidence(synthetic_records, monkeypatch)
    request = {"merchant_id": str(merchant_id), "action": "send_delivery_update",
               "idempotency_key": "delivery-update-idem-01"}
    created = client.post(f"/api/cases/{case_id}/actions", json=request)
    assert created.status_code == 200, created.text
    action = created.json()
    assert action["approval_status"] == "NOT_REQUIRED"
    assert action["execution_status"] == "READY"
    replay = client.post(f"/api/cases/{case_id}/actions", json=request)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["intervention_id"] == action["intervention_id"]
    another_open_action = client.post(f"/api/cases/{case_id}/actions", json={
        **request, "idempotency_key": "delivery-update-idem-02",
    })
    assert another_open_action.status_code == 409
    collision = client.post(f"/api/cases/{case_id}/actions", json={**request, "action": "create_support_case"})
    assert collision.status_code == 409

    payload = {"merchant_id": str(merchant_id)}
    executed = client.post(f"/api/cases/{case_id}/interventions/{action['intervention_id']}/execute", json=payload)
    assert executed.status_code == 200, executed.text
    assert executed.json()["simulation_result"]["external_side_effect"] is False
    assert executed.json()["simulation_result"]["outcome_status"] == "OBSERVING"
    outcomes = client.get(f"/api/cases/{case_id}/outcomes", params={"merchant_id": str(merchant_id)})
    assert outcomes.status_code == 200 and len(outcomes.json()["items"]) == 1
    assert outcomes.json()["items"][0]["dispute_occurred"] is None
    refreshed = client.post(f"/api/cases/{case_id}/outcomes/refresh", params={"merchant_id": str(merchant_id)})
    assert refreshed.status_code == 200
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        conn.execute("""
            INSERT INTO disputes(merchant_id,transaction_id,customer_id,reason,amount,status,created_at)
            VALUES (%s,%s,%s,'TEST','20000','OPEN',now())
        """, (merchant_id, synthetic_records[2], synthetic_records[1]))
    observed = client.post(f"/api/cases/{case_id}/outcomes/refresh", params={"merchant_id": str(merchant_id)})
    assert observed.status_code == 200
    outcomes = client.get(f"/api/cases/{case_id}/outcomes", params={"merchant_id": str(merchant_id)}).json()["items"]
    assert outcomes[0]["dispute_occurred"] is True
    assert "outcome_prevented" not in str(outcomes)
    replayed_execution = client.post(f"/api/cases/{case_id}/interventions/{action['intervention_id']}/execute", json=payload)
    assert replayed_execution.status_code == 200 and replayed_execution.json()["replayed"] is True


def test_human_approval_is_required_before_high_value_simulation(synthetic_records, monkeypatch):
    client, merchant_id, case_id = _prepare_case_with_evidence(synthetic_records, monkeypatch)
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        conn.execute("UPDATE merchant_policies SET high_value_threshold=10000 WHERE merchant_id=%s", (merchant_id,))
    request = {"merchant_id": str(merchant_id), "action": "send_delivery_update",
               "idempotency_key": "delivery-update-approval-01"}
    created = client.post(f"/api/cases/{case_id}/actions", json=request)
    assert created.status_code == 200, created.text
    action = created.json()
    assert action["approval_status"] == "PENDING"
    assert action["execution_status"] == "PENDING_APPROVAL"
    payload = {"merchant_id": str(merchant_id)}
    blocked = client.post(f"/api/cases/{case_id}/interventions/{action['intervention_id']}/execute", json=payload)
    assert blocked.status_code == 409

    approval = {"merchant_id": str(merchant_id), "decision": "APPROVED", "actor_id": "reviewer-test"}
    approved = client.post(f"/api/cases/{case_id}/interventions/{action['intervention_id']}/approval", json=approval)
    assert approved.status_code == 200 and approved.json()["execution_status"] == "READY"
    replay = client.post(f"/api/cases/{case_id}/interventions/{action['intervention_id']}/approval", json=approval)
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    executed = client.post(f"/api/cases/{case_id}/interventions/{action['intervention_id']}/execute", json=payload)
    assert executed.status_code == 200, executed.text
    assert executed.json()["execution_status"] == "EXECUTED"


def test_merchant_incident_detection_and_overlap_conflict(synthetic_records):
    merchant_id, customer_id, transaction_id = synthetic_records
    with psycopg.connect(os.environ["TEST_DATABASE_URL"]) as conn:
        conn.execute("""
            UPDATE merchant_policies SET incident_min_disputes=3,incident_min_transactions=1
            WHERE merchant_id=%s
        """, (merchant_id,))
        conn.execute("""
            INSERT INTO disputes(merchant_id,transaction_id,customer_id,reason,amount,status,created_at)
            SELECT %s,%s,%s,'TEST',20000,'OPEN',now() FROM generate_series(1,3)
        """, (merchant_id, transaction_id, customer_id))
    client = TestClient(app)
    params = {"merchant_id": str(merchant_id), "window_days": 7, "baseline_days": 30}
    detected = client.post("/api/incidents/detect", json=params)
    assert detected.status_code == 200, detected.text
    first = detected.json()
    assert first["triggered"] is True and first["conflict"] is False
    duplicate = client.post("/api/incidents/detect", json=params)
    assert duplicate.status_code == 200 and duplicate.json()["conflict"] is True
    assert duplicate.json()["incident_id"] == first["incident_id"]
    listing = client.get("/api/incidents", params={"merchant_id": str(merchant_id)})
    assert listing.status_code == 200 and len(listing.json()["items"]) == 1
    changed = client.post(f"/api/incidents/{first['incident_id']}/status", json={
        "merchant_id": str(merchant_id), "status": "ACKNOWLEDGED", "actor_id": "reviewer",
    })
    assert changed.status_code == 200 and changed.json()["status"] == "ACKNOWLEDGED"
    hidden = client.get("/api/incidents", params={"merchant_id": str(uuid4())})
    assert hidden.status_code == 200 and hidden.json()["items"] == []
