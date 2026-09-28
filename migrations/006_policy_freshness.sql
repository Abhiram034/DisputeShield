ALTER TABLE merchant_policies
    ADD COLUMN evidence_max_age_minutes INT NOT NULL DEFAULT 60
        CHECK (evidence_max_age_minutes BETWEEN 1 AND 10080);
