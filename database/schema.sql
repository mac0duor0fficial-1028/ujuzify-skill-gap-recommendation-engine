-- Ujuzify data platform: raw -> staging -> curated -> analytics (PostgreSQL 14+)
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS curated;
CREATE SCHEMA IF NOT EXISTS analytics;
CREATE SCHEMA IF NOT EXISTS app;

-- RAW: source records with provenance -------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.job_postings (
    source_id        TEXT        NOT NULL,
    source_job_id    TEXT        NOT NULL,
    source_url       TEXT,
    title            TEXT        NOT NULL CHECK (length(btrim(title)) > 0),
    company_name     TEXT,
    location_raw     TEXT,
    country_code     CHAR(2),
    city             TEXT,
    description_raw  TEXT,
    employment_type  TEXT,
    seniority        TEXT,
    education_required  TEXT,
    experience_required TEXT,
    salary_min       NUMERIC,
    salary_max       NUMERIC,
    currency         TEXT,
    posted_at        TIMESTAMPTZ,
    retrieved_at     TIMESTAMPTZ NOT NULL,
    source_category  TEXT,
    raw_payload_path TEXT        NOT NULL,
    content_hash     TEXT        NOT NULL,
    PRIMARY KEY (source_id, source_job_id)
);

-- STAGING: cleaned, normalised, de-duplicated ----------------------------------------
CREATE TABLE IF NOT EXISTS staging.job_postings (
    job_id           TEXT PRIMARY KEY,
    source_id        TEXT NOT NULL,
    source_job_id    TEXT NOT NULL,
    title            TEXT NOT NULL,
    company_name     TEXT,
    location_raw     TEXT,
    country_code     CHAR(2) NOT NULL,
    description      TEXT,
    posted_at        TIMESTAMPTZ,
    retrieved_at     TIMESTAMPTZ NOT NULL,
    content_hash     TEXT NOT NULL UNIQUE,
    UNIQUE (source_id, source_job_id)
);

-- CURATED: canonical entities -------------------------------------------------------
CREATE TABLE IF NOT EXISTS curated.skills (
    skill_id       TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    category       TEXT NOT NULL,
    aliases        TEXT[] NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS curated.careers (
    career_id      TEXT PRIMARY KEY,
    career_name    TEXT NOT NULL UNIQUE,
    career_family  TEXT NOT NULL,
    description    TEXT
);
CREATE TABLE IF NOT EXISTS curated.job_postings (
    job_id         TEXT PRIMARY KEY REFERENCES staging.job_postings(job_id),
    title          TEXT NOT NULL,
    company_name   TEXT,
    city           TEXT,
    country_code   CHAR(2) NOT NULL CHECK (country_code = 'KE'),
    salary_min     NUMERIC,
    salary_max     NUMERIC,
    CHECK (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max),
    candidate_career_id TEXT REFERENCES curated.careers(career_id)
);
CREATE TABLE IF NOT EXISTS curated.job_skills (
    job_id         TEXT NOT NULL REFERENCES curated.job_postings(job_id) ON DELETE CASCADE,
    skill_id       TEXT NOT NULL REFERENCES curated.skills(skill_id),
    matched_text   TEXT,
    context        TEXT,
    confidence     NUMERIC CHECK (confidence BETWEEN 0 AND 1),
    PRIMARY KEY (job_id, skill_id)
);
CREATE TABLE IF NOT EXISTS curated.career_skills (
    career_id      TEXT NOT NULL REFERENCES curated.careers(career_id) ON DELETE CASCADE,
    skill_id       TEXT NOT NULL REFERENCES curated.skills(skill_id),
    importance     NUMERIC NOT NULL CHECK (importance BETWEEN 0 AND 1),
    PRIMARY KEY (career_id, skill_id)
);

-- ANALYTICS ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analytics.skill_demand (
    skill_id       TEXT PRIMARY KEY REFERENCES curated.skills(skill_id),
    job_count      INTEGER NOT NULL CHECK (job_count >= 0),
    total_jobs     INTEGER NOT NULL CHECK (total_jobs > 0),
    demand_rate    NUMERIC NOT NULL CHECK (demand_rate BETWEEN 0 AND 1),
    calculated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- APP: product data -------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app.recommendation_feedback (
    id             BIGSERIAL PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    profile_id     TEXT NOT NULL,
    career_id      TEXT NOT NULL,
    feedback_type  TEXT NOT NULL CHECK (feedback_type IN ('useful','not_useful','accepted','rejected')),
    comment        TEXT
);

CREATE INDEX IF NOT EXISTS idx_staging_title        ON staging.job_postings (lower(title));
CREATE INDEX IF NOT EXISTS idx_curated_jobs_city    ON curated.job_postings (city);
CREATE INDEX IF NOT EXISTS idx_curated_jobs_career  ON curated.job_postings (candidate_career_id);
CREATE INDEX IF NOT EXISTS idx_job_skills_skill     ON curated.job_skills (skill_id);
CREATE INDEX IF NOT EXISTS idx_feedback_profile     ON app.recommendation_feedback (profile_id);
