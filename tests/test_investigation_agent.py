import json
from uuid import uuid4

from apps.api.app.domain.investigation_agent import investigate_case
from apps.api.app.domain.investigation_tools import ToolResult


class StubTools:
    def get_transaction(self, transaction_id):
        return ToolResult("get_transaction", "KNOWN", {"id": transaction_id, "status": "succeeded"})


class StubProvider:
    def __init__(self, transaction_id):
        self.transaction_id = str(transaction_id)
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            assert all(item["function"]["strict"] for item in tools)
            return {"tool_calls": [{"id": "call-1", "function": {
                "name": "get_transaction", "arguments": "{}",
            }}]}
        ref = f"get_transaction:{self.transaction_id}:status"
        return {"content": json.dumps({
            "risk_level": "MEDIUM", "primary_hypothesis": "Delivery issue may explain the dispute",
            "supporting_evidence": [{"statement": "Payment succeeded", "kind": "FACT", "evidence_refs": [ref]}],
            "contradicting_evidence": [], "unknowns": ["Delivery confirmation unavailable"],
            "recommended_action": "flag_human_review", "confidence": 0.62,
        })}


def test_investigation_report_cites_collected_evidence(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    tx, customer = uuid4(), uuid4()
    provider = StubProvider(tx)
    status, report = investigate_case(StubTools(), tx, customer, "MEDIUM", provider)
    assert status == "COMPLETED"
    assert report["supporting_evidence"][0]["evidence_refs"] == [f"get_transaction:{tx}:status"]
    assert provider.calls == 2


def test_investigation_rejects_fabricated_evidence_reference(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    tx, customer = uuid4(), uuid4()

    class FabricatingProvider(StubProvider):
        def complete(self, messages, tools):
            result = super().complete(messages, tools)
            if self.calls == 2:
                data = json.loads(result["content"])
                data["supporting_evidence"][0]["evidence_refs"] = ["missing:source:field"]
                result["content"] = json.dumps(data)
            return result

    status, report = investigate_case(StubTools(), tx, customer, "MEDIUM", FabricatingProvider(tx))
    assert status == "UNAVAILABLE" and report is None
