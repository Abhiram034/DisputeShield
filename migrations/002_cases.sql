CREATE TABLE cases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id),
    transaction_id UUID NOT NULL UNIQUE REFERENCES transactions(id),
    risk_assessment_id UUID NOT NULL REFERENCES risk_assessments(id),
    state TEXT NOT NULL DEFAULT 'DETECTED'
        CHECK (state IN ('DETECTED','INVESTIGATING','INVESTIGATION_COMPLETE','ACTION_RECOMMENDED','WAITING_FOR_APPROVAL','APPROVED','EXECUTING','EXECUTED','MONITORING','RESOLVED','ESCALATED','EXPIRED','FAILED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);
CREATE INDEX cases_merchant_state_time ON cases(merchant_id,state,created_at DESC);
CREATE INDEX cases_merchant_risk_time ON cases(merchant_id,created_at DESC);
