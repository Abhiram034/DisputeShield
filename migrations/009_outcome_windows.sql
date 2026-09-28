ALTER TABLE merchant_policies
    ADD COLUMN outcome_observation_days INT NOT NULL DEFAULT 30
        CHECK (outcome_observation_days BETWEEN 1 AND 365);

ALTER TABLE outcomes
    ADD COLUMN window_started_at TIMESTAMPTZ,
    ADD COLUMN window_ends_at TIMESTAMPTZ,
    ADD COLUMN outcome_status TEXT NOT NULL DEFAULT 'OBSERVING'
        CHECK (outcome_status IN ('OBSERVING','COMPLETE','INCONCLUSIVE','CANCELED')),
    ADD COLUMN last_observed_at TIMESTAMPTZ,
    ADD COLUMN observation_evidence JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE outcomes
SET window_started_at=created_at,
    window_ends_at=created_at+observation_window,
    outcome_status=CASE WHEN created_at+observation_window<=now() THEN 'COMPLETE' ELSE 'OBSERVING' END,
    last_observed_at=created_at;

ALTER TABLE outcomes
    ALTER COLUMN window_started_at SET NOT NULL,
    ALTER COLUMN window_ends_at SET NOT NULL;

CREATE UNIQUE INDEX outcomes_one_per_intervention
    ON outcomes(intervention_id) WHERE intervention_id IS NOT NULL;
CREATE INDEX outcomes_window_status_end
    ON outcomes(outcome_status,window_ends_at);
