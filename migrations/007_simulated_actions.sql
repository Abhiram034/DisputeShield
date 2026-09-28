ALTER TABLE interventions ADD COLUMN merchant_id UUID REFERENCES merchants(id);
UPDATE interventions i
SET merchant_id=t.merchant_id
FROM transactions t
WHERE t.id=i.transaction_id AND i.merchant_id IS NULL;
ALTER TABLE interventions ALTER COLUMN merchant_id SET NOT NULL;
ALTER TABLE interventions ADD COLUMN request_fingerprint CHAR(64) NOT NULL DEFAULT repeat('0',64);
ALTER TABLE interventions ADD COLUMN requested_by TEXT NOT NULL DEFAULT 'system';
ALTER TABLE interventions ADD COLUMN simulation_result JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE interventions ADD CONSTRAINT intervention_merchant_idempotency_unique
    UNIQUE (merchant_id,idempotency_key);
ALTER TABLE interventions ADD CONSTRAINT intervention_approval_status_check
    CHECK (approval_status IN ('NOT_REQUIRED','PENDING','APPROVED','REJECTED'));
ALTER TABLE interventions ADD CONSTRAINT intervention_execution_status_check
    CHECK (execution_status IN ('PENDING_APPROVAL','READY','EXECUTED','BLOCKED'));

CREATE TABLE intervention_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    intervention_id UUID NOT NULL REFERENCES interventions(id) ON DELETE CASCADE,
    merchant_id UUID NOT NULL REFERENCES merchants(id),
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED','REJECTED')),
    actor_id TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (intervention_id)
);
CREATE INDEX intervention_approvals_tenant_time
    ON intervention_approvals(merchant_id,created_at DESC);
