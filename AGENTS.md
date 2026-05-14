# Repository Guidelines

## Project Structure & Module Organization

- `api/`: FastAPI app and routers for anomaly, prediction, upload, and meter metadata endpoints.
- `scripts/`: executable prototype scripts and full pipelines, for example `pipeline_full.py` and `predict_h1z16.py`.
- `config/`: meter metadata and configuration helpers.
- `src/`: reusable modules for DB access, preprocessing, anomaly detection, forecasting, and RAG.
- `notebooks/`: EDA and comparison notebooks.
- `outputs/`: generated CSVs, plots, and report artifacts. Treat as derived output.
- `tests/`: reserved for automated tests.

## Build, Test, and Development Commands

- Activate the main environment:
  ```bash
  cd /home/playdata2/final_pj && source .venv/bin/activate
  ```
- Run the API locally:
  ```bash
  PYTHONPATH=/home/playdata2/final_pj/energy-platform uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
  ```
- Run the main anomaly pipeline:
  ```bash
  PYTHONPATH=/home/playdata2/final_pj/energy-platform python energy-platform/scripts/pipeline_full.py
  ```
- Syntax-check a file:
  ```bash
  python -m py_compile path/to/file.py
  ```

## Coding Style & Naming Conventions

- Use Python with 4-space indentation and type hints where practical.
- Prefer small, explicit functions over large monolithic scripts.
- Keep DataFrame column names stable and descriptive.
- Use task-oriented filenames such as `anomaly_if_h1z16.py` or `eda_meter_profile.ipynb`.
- Default to ASCII unless the file already contains Korean labels or comments.

## Testing Guidelines

- No full test suite is established yet; verify changes with focused script runs, API checks, and `py_compile`.
- When changing data pipelines, log shapes, columns, and a short sample output.
- Add future tests under `tests/` using `test_<module>.py`.

## Commit & Pull Request Guidelines

- Use short imperative commit messages, for example `Add anomaly detail CSV export`.
- In PRs, include purpose, affected paths, verification commands, and screenshots or output file paths when UI/plots change.

## Security & Configuration Tips

- Load secrets from `.env`; never hardcode DB credentials.
- Assume the database is read-only unless explicitly told otherwise.
- Do not manually edit generated files in `outputs/` unless the task explicitly requires it.
