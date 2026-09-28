ALTER TABLE merchant_policies
    ADD COLUMN incident_min_disputes INT NOT NULL DEFAULT 3 CHECK (incident_min_disputes BETWEEN 1 AND 10000),
    ADD COLUMN incident_rate_multiplier NUMERIC(8,3) NOT NULL DEFAULT 2.000
        CHECK (incident_rate_multiplier BETWEEN 1.001 AND 1000.000),
    ADD COLUMN incident_min_transactions INT NOT NULL DEFAULT 10 CHECK (incident_min_transactions BETWEEN 1 AND 1000000);

CREATE TABLE merchant_incidents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id),
    incident_type TEXT NOT NULL CHECK (incident_type IN ('DISPUTE_RATE_SPIKE')),
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACKNOWLEDGED','RESOLVED')),
    window_started_at TIMESTAMPTZ NOT NULL,
    window_ends_at TIMESTAMPTZ NOT NULL,
    current_disputes INT NOT NULL CHECK (current_disputes >= 0),
    current_transactions INT NOT NULL CHECK (current_transactions >= 0),
    baseline_disputes INT NOT NULL CHECK (baseline_disputes >= 0),
    baseline_transactions INT NOT NULL CHECK (baseline_transactions >= 0),
    current_dispute_rate NUMERIC(12,8),
    baseline_dispute_rate NUMERIC(12,8),
    rate_multiplier NUMERIC(12,4),
    detection_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    acknowledged_by TEXT,
    resolution_note TEXT,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (window_ends_at > window_started_at)
);

CREATE INDEX merchant_incidents_scope_status ON merchant_incidents(merchant_id,status,created_at DESC);
CREATE INDEX merchant_incidents_window ON merchant_incidents(merchant_id,window_started_at,window_ends_at);
