"""Daily batch workflow: ingest -> validate -> transform -> NLP -> demand/labels -> load -> recommend -> evaluate."""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

default_args = {"owner": "ujuzify", "retries": 2, "retry_delay": timedelta(minutes=5)}


def _ingest():
    from src.ingestion.job_api import run
    return run()[2]


def _validate():
    from src.preprocessing.validate_jobs import validate
    rep = validate()
    if not rep["ok"]:
        raise ValueError(rep["errors"])


def _transform():
    from src.etl.transform_jobs import transform
    return transform()


def _extract():
    from src.nlp.extract_job_skills import run
    return run()


def _demand():
    from src.etl.calculate_skill_demand import calculate
    return calculate()


def _labels():
    from src.etl.label_careers import label
    return label()


def _load():
    from src.etl.load_postgres import load
    return load()


def _evaluate_extraction():
    from src.nlp.evaluate_skill_extraction import evaluate
    return evaluate()


def _recommend():
    from src.recommendation.run_recommendation import run
    from src import config
    return str(run(config.DATA_DIR / "graduate_profiles" / "example_profile.csv"))


def _evaluate_recs():
    from src.recommendation.evaluate_recommendations import evaluate
    from src import config
    return evaluate(config.DATA_DIR / "graduate_profiles" / "example_profile.csv", config.EVALUATION_DIR / "career_recommendation_gold.csv")


with DAG("ujuzify_pipeline", default_args=default_args, start_date=datetime(2026, 1, 1), schedule="@daily",
         catchup=False, tags=["ujuzify"]) as dag:
    ingest = PythonOperator(task_id="ingest", python_callable=_ingest)
    validate = PythonOperator(task_id="validate", python_callable=_validate)
    transform = PythonOperator(task_id="transform", python_callable=_transform)
    extract = PythonOperator(task_id="extract_skills", python_callable=_extract)
    demand = PythonOperator(task_id="skill_demand", python_callable=_demand)
    labels = PythonOperator(task_id="career_labels", python_callable=_labels)
    eval_x = PythonOperator(task_id="evaluate_extraction", python_callable=_evaluate_extraction)
    load = PythonOperator(task_id="load_postgres", python_callable=_load)
    recommend = PythonOperator(task_id="recommend", python_callable=_recommend)
    eval_r = PythonOperator(task_id="evaluate_recommendations", python_callable=_evaluate_recs)

    ingest >> validate >> transform >> extract >> [demand, labels, eval_x]
    [demand, labels] >> load >> recommend >> eval_r
