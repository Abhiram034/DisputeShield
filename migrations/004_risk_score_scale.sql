ALTER TABLE risk_assessments
    DROP CONSTRAINT risk_assessments_risk_score_check;
ALTER TABLE risk_assessments
    ALTER COLUMN risk_score TYPE NUMERIC(5,2)
    USING (risk_score * 100)::NUMERIC(5,2);
ALTER TABLE risk_assessments
    ADD CONSTRAINT risk_assessments_risk_score_check
    CHECK (risk_score >= 0 AND risk_score <= 100);
