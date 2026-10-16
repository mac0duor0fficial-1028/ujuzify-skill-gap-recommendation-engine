-- Ujuzify physical schema, Phase 3. Implements the jobs/careers/skills part of docs/data_model.md.
-- Idempotent: safe to run repeatedly. Apply with:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/schema.sql
--
-- Layers: staging (CSV mirrors, loaded as-is) -> curated (typed, constrained, FK-linked)
--         -> analytics (derived statistics). Raw JSONL stays on disk in data/raw/.
-- learning_resources arrives with Phase 6, profile and recommendation tables with Phase 7.

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS curated;
CREATE SCHEMA IF NOT EXISTS analytics;

-- ---------------------------------------------------------------- staging (mirror data/interim/staging_jobs.csv
-- and data/processed/job_labels.csv; column order matches the CSV headers exactly)
CREATE TABLE IF NOT EXISTS staging.jobs (
    job_id              text,
    source_id           text,
    source_job_id       text,
    source_url          text,
    title               text,
    title_norm          text,
    company             text,
    location            text,
    country             text,
    description         text,
    description_chars   integer,
    employment_type     text,
    experience_required text,
    education_required  text,
    date_posted         timestamptz,
    retrieved_at        timestamptz,
    content_hash        text,
    is_synthetic        boolean,
    repost_count        integer
);

CREATE TABLE IF NOT EXISTS staging.job_labels (
    job_id        text,
    career_id     text,
    career_name   text,
    label_status  text,
    label_method  text,
    matched_alias text,
    is_synthetic  boolean
);

CREATE TABLE IF NOT EXISTS staging.job_skills (
    job_id            text,
    skill_id          text,
    canonical_name    text,
    importance        numeric,
    requirement_level text,
    evidence          text,
    rule              text,
    mentions          integer,
    method            text,
    is_synthetic      boolean
);

CREATE TABLE IF NOT EXISTS staging.career_skills (
    career_id                    text,
    career_name                  text,
    skill_id                     text,
    canonical_name               text,
    category                     text,
    postings_with_skill          integer,
    postings_in_career           integer,
    frequency                    numeric,
    importance                   numeric,
    low_sample                   boolean,
    synthetic_postings_in_career integer,
    method                       text
);

CREATE TABLE IF NOT EXISTS staging.skill_demand (
    scope                       text,
    scope_name                  text,
    skill_id                    text,
    canonical_name              text,
    category                    text,
    postings_with_skill         integer,
    postings_in_scope           integer,
    share                       numeric,
    low_sample                  boolean,
    synthetic_postings_in_scope integer,
    method                      text
);

-- ---------------------------------------------------------------- curated: reference vocabularies
CREATE TABLE IF NOT EXISTS curated.careers (
    career_id     text PRIMARY KEY CHECK (career_id ~ '^CA[0-9]{2}$'),
    career_name   text NOT NULL UNIQUE,
    career_family text NOT NULL,
    description   text NOT NULL
);

CREATE TABLE IF NOT EXISTS curated.skills (
    skill_id       text PRIMARY KEY CHECK (skill_id ~ '^SK[0-9]{2}$'),
    canonical_name text NOT NULL UNIQUE CHECK (canonical_name ~ '^[a-z][a-z0-9_]*$'),
    display_name   text NOT NULL,
    category       text NOT NULL,
    aliases        text NOT NULL DEFAULT ''   -- pipe-separated, as in skill_taxonomy.csv
);

-- ---------------------------------------------------------------- curated: postings and labels
CREATE TABLE IF NOT EXISTS curated.job_postings (
    job_id              text PRIMARY KEY,
    source_id           text NOT NULL,
    source_job_id       text NOT NULL,
    source_url          text,
    title               text NOT NULL CHECK (btrim(title) <> ''),
    company             text,
    location            text,
    country             text NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
    description         text NOT NULL CHECK (btrim(description) <> ''),
    employment_type     text,
    experience_required text,
    education_required  text,
    date_posted         timestamptz,
    retrieved_at        timestamptz NOT NULL,
    content_hash        text NOT NULL,
    is_synthetic        boolean NOT NULL,
    repost_count        integer NOT NULL DEFAULT 0 CHECK (repost_count >= 0),
    UNIQUE (source_id, source_job_id)
);
CREATE INDEX IF NOT EXISTS job_postings_content_hash_idx ON curated.job_postings (content_hash);
CREATE INDEX IF NOT EXISTS job_postings_date_posted_idx  ON curated.job_postings (date_posted);

-- Weak, title-derived labels (not gold labels). career_id is NULL when label_status = 'unmapped'.
CREATE TABLE IF NOT EXISTS curated.job_career_labels (
    job_id        text PRIMARY KEY REFERENCES curated.job_postings (job_id) ON DELETE CASCADE,
    career_id     text REFERENCES curated.careers (career_id),
    label_status  text NOT NULL CHECK (label_status IN ('labeled', 'unmapped')),
    label_method  text NOT NULL,
    matched_alias text,
    CHECK ((label_status = 'labeled') = (career_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS job_career_labels_career_idx ON curated.job_career_labels (career_id);

-- Skills extracted from postings (Phase 4). importance is a heuristic from explicit wording
-- ("a plus" = 0.5, otherwise 1.0), not a measured weight. evidence/rule keep each row traceable.
CREATE TABLE IF NOT EXISTS curated.job_skills (
    job_id            text    NOT NULL REFERENCES curated.job_postings (job_id) ON DELETE CASCADE,
    skill_id          text    NOT NULL REFERENCES curated.skills (skill_id),
    importance        numeric NOT NULL CHECK (importance > 0 AND importance <= 1),
    requirement_level text    NOT NULL CHECK (requirement_level IN ('required', 'preferred', 'unspecified')),
    evidence          text    NOT NULL,
    rule              text    NOT NULL,
    mentions          integer NOT NULL CHECK (mentions >= 1),
    method            text    NOT NULL,
    PRIMARY KEY (job_id, skill_id)
);
CREATE INDEX IF NOT EXISTS job_skills_skill_idx ON curated.job_skills (skill_id);

-- Career skill profiles (Phase 5): what sampled postings for each career ask for.
-- frequency = share of the career's postings mentioning the skill; importance = mean posting-level
-- importance (<= frequency). Sample statistics, not a ranking of the labour market.
CREATE TABLE IF NOT EXISTS curated.career_skills (
    career_id                    text    NOT NULL REFERENCES curated.careers (career_id),
    skill_id                     text    NOT NULL REFERENCES curated.skills (skill_id),
    postings_with_skill          integer NOT NULL CHECK (postings_with_skill > 0),
    postings_in_career           integer NOT NULL CHECK (postings_in_career > 0),
    frequency                    numeric NOT NULL CHECK (frequency > 0 AND frequency <= 1),
    importance                   numeric NOT NULL CHECK (importance > 0 AND importance <= 1),
    low_sample                   boolean NOT NULL,
    synthetic_postings_in_career integer NOT NULL CHECK (synthetic_postings_in_career >= 0),
    method                       text    NOT NULL,
    PRIMARY KEY (career_id, skill_id),
    CHECK (importance <= frequency),
    CHECK (postings_with_skill <= postings_in_career)
);
CREATE INDEX IF NOT EXISTS career_skills_skill_idx ON curated.career_skills (skill_id);

-- NOT created here: graduate_profiles, profile_skills, recommendations (data_model.md). They hold
-- user data and the audit trail of recommendations (model_version, created_at), so they must
-- survive the full-refresh reload in load.sql. They arrive with the Phase 7 API that writes them;
-- until then recommendations are stored in data/processed/recommendations.csv.

-- ---------------------------------------------------------------- analytics
-- scope is 'all' or a career_id. Shares are sample statistics from the acquired corpus,
-- not a ranking of the labour market; low_sample and synthetic counts must travel with them.
CREATE TABLE IF NOT EXISTS analytics.skill_demand (
    scope                       text    NOT NULL CHECK (scope = 'all' OR scope ~ '^CA[0-9]{2}$'),
    scope_name                  text    NOT NULL,
    skill_id                    text    NOT NULL REFERENCES curated.skills (skill_id),
    postings_with_skill         integer NOT NULL CHECK (postings_with_skill > 0),
    postings_in_scope           integer NOT NULL CHECK (postings_in_scope > 0),
    share                       numeric NOT NULL CHECK (share > 0 AND share <= 1),
    low_sample                  boolean NOT NULL,
    synthetic_postings_in_scope integer NOT NULL CHECK (synthetic_postings_in_scope >= 0),
    method                      text    NOT NULL,
    PRIMARY KEY (scope, skill_id),
    CHECK (postings_with_skill <= postings_in_scope)
);
