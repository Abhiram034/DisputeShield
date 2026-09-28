ALTER TABLE risk_assessments
    ALTER COLUMN intervention_opportunity TYPE TEXT
    USING CASE WHEN intervention_opportunity THEN 'HIGH' ELSE 'LOW' END;
ALTER TABLE risk_assessments
    ADD CONSTRAINT risk_assessments_opportunity_check
    CHECK (intervention_opportunity IN ('UNKNOWN','LOW','MEDIUM','HIGH'));
