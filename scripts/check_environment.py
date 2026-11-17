"""Verify runtime dependencies, reference data and (optionally) the built UI. Exit code 1 on failure."""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REQUIRED = ["fastapi", "uvicorn", "pydantic", "httpx", "pypdf", "multipart", "prometheus_client", "yaml"]
OPTIONAL = {"spacy": "PhraseMatcher backend", "psycopg": "PostgreSQL loading", "mlflow": "experiment tracking"}


def main() -> int:
    bad = 0
    for m in REQUIRED:
        try:
            importlib.import_module(m)
            print(f"ok       {m}")
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print(f"MISSING  {m}: {exc}")
    for m, why in OPTIONAL.items():
        try:
            importlib.import_module(m)
            print(f"ok       {m} (optional: {why})")
        except Exception:  # noqa: BLE001
            print(f"absent   {m} (optional: {why})")
    from src import config
    from src.taxonomy import load_taxonomy
    try:
        t = load_taxonomy()
        assert len(t.skills) >= 70 and len(t.careers) >= 9
        print(f"ok       taxonomy: {len(t.skills)} skills, {len(t.careers)} careers")
    except Exception as exc:  # noqa: BLE001
        bad += 1
        print(f"BROKEN   taxonomy: {exc}")
    for p in (config.LEARNING_RESOURCES_CSV, config.PROJECTS_CSV):
        print(("ok       " if p.exists() else "MISSING  ") + str(p.relative_to(config.ROOT)))
        bad += not p.exists()
    ui = config.ROOT / "app" / "dist" / "index.html"
    print(("ok       " if ui.exists() else "warning  ") + "built UI (app/dist/index.html)" + ("" if ui.exists() else " not found; run `npm ci && npm run build` in app/"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
