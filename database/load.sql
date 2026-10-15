-- Full refresh load of Phase 3 outputs. Run from the PROJECT ROOT after schema.sql:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f database/schema.sql -f database/load.sql
-- Paths are relative to the directory psql is started in. All-or-nothing (one transaction).

BEGIN;

TRUNCATE analytics.skill_demand, curated.job_skills, curated.job_career_labels, curated.job_postings,
         curated.skills, curated.careers,
         staging.jobs, staging.job_labels, staging.job_skills, staging.skill_demand;

\copy curated.careers FROM 'data/external/career_taxonomy.csv' WITH (FORMAT csv, HEADER true)
\copy curated.skills  FROM 'data/external/skill_taxonomy.csv'  WITH (FORMAT csv, HEADER true)

\copy staging.jobs         FROM 'data/interim/staging_jobs.csv'    WITH (FORMAT csv, HEADER true)
\copy staging.job_labels   FROM 'data/processed/job_labels.csv'    WITH (FORMAT csv, HEADER true)
\copy staging.job_skills   FROM 'data/processed/job_skills.csv'    WITH (FORMAT csv, HEADER true)
\copy staging.skill_demand FROM 'data/processed/skill_demand.csv'  WITH (FORMAT csv, HEADER true)

INSERT INTO curated.job_postings
    (job_id, source_id, source_job_id, source_url, title, company, location, country, description,
     employment_type, experience_required, education_required, date_posted, retrieved_at,
     content_hash, is_synthetic, repost_count)
SELECT job_id, source_id, source_job_id, source_url, title, company, location, country, description,
       employment_type, experience_required, education_required, date_posted, retrieved_at,
       content_hash, is_synthetic, repost_count
FROM staging.jobs;

INSERT INTO curated.job_career_labels (job_id, career_id, label_status, label_method, matched_alias)
SELECT job_id, career_id, label_status, label_method, matched_alias
FROM staging.job_labels;

INSERT INTO curated.job_skills
    (job_id, skill_id, importance, requirement_level, evidence, rule, mentions, method)
SELECT job_id, skill_id, importance, requirement_level, evidence, rule, mentions, method
FROM staging.job_skills;

INSERT INTO analytics.skill_demand
    (scope, scope_name, skill_id, postings_with_skill, postings_in_scope, share, low_sample,
     synthetic_postings_in_scope, method)
SELECT scope, scope_name, skill_id, postings_with_skill, postings_in_scope, share, low_sample,
       synthetic_postings_in_scope, method
FROM staging.skill_demand;

COMMIT;

-- Row counts for a quick sanity check.
SELECT 'careers' AS tbl, count(*) FROM curated.careers
UNION ALL SELECT 'skills', count(*) FROM curated.skills
UNION ALL SELECT 'job_postings', count(*) FROM curated.job_postings
UNION ALL SELECT 'job_career_labels', count(*) FROM curated.job_career_labels
UNION ALL SELECT 'job_skills', count(*) FROM curated.job_skills
UNION ALL SELECT 'skill_demand', count(*) FROM analytics.skill_demand;
