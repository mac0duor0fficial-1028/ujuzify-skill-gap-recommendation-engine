"""Log baseline evaluation metrics to MLflow (experiment: graduate-skill-gap-recommendation).

The recommender is a deterministic baseline, so this records evaluation lineage rather than a model.
Requires `pip install mlflow`; set MLFLOW_TRACKING_URI for a remote server.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config  # noqa: E402


def main() -> int:
    try:
        import mlflow
    except ImportError:
        print("mlflow is not installed (pip install -r requirements-mlops.txt)")
        return 1
    from src.recommendation.evaluate_recommendations import evaluate
    metrics_csv = config.EVALUATION_DIR / "career_recommendation_metrics.csv"
    evaluate(config.DATA_DIR / "graduate_profiles" / "example_profile.csv", config.EVALUATION_DIR / "career_recommendation_gold.csv", metrics_csv)
    mlflow.set_experiment("graduate-skill-gap-recommendation")
    with mlflow.start_run(run_name=config.MODEL_VERSION):
        mlflow.log_param("model_version", config.MODEL_VERSION)
        mlflow.log_param("coverage_weight", config.COVERAGE_WEIGHT)
        mlflow.log_param("market_weight", config.MARKET_WEIGHT)
        for r in csv.DictReader(open(metrics_csv)):
            mlflow.log_metric(r["metric"], float(r["value"]))
        mlflow.log_artifact(str(metrics_csv))
    print("logged baseline metrics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
