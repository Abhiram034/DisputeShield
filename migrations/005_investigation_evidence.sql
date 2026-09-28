CREATE TABLE investigation_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    investigation_id UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    merchant_id UUID NOT NULL REFERENCES merchants(id),
    evidence_ref TEXT NOT NULL,
    source_tool TEXT NOT NULL,
    source_record_id TEXT,
    field_name TEXT NOT NULL,
    value JSONB,
    data_status TEXT NOT NULL CHECK (data_status IN ('KNOWN','EMPTY','FAILED')),
    observed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (investigation_id, evidence_ref)
);
CREATE INDEX investigation_evidence_tenant_time
    ON investigation_evidence(merchant_id, investigation_id, created_at DESC);
