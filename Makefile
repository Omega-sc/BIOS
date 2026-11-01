UV ?= uv
PYTHON ?= $(UV) run python
RUFF ?= $(UV) run ruff
PYTEST ?= $(UV) run pytest
STREAMLIT ?= $(UV) run streamlit

.PHONY: all install format lint lint-hardcode blind-check test check smoke dataset calibrate eval demo-replay clean

install:
	$(UV) pip install -r requirements.txt

format:
	$(RUFF) format .

lint:
	$(RUFF) check .

lint-hardcode:
	$(PYTHON) eval/lint_hardcode.py

blind-check:
	$(PYTHON) eval/blind/blind_check.py

test:
	$(PYTEST) -v tests/

check:
	$(RUFF) check .
	$(RUFF) format --check .
	$(PYTHON) eval/lint_hardcode.py
	$(PYTHON) eval/blind/blind_check.py
	$(PYTEST) -v tests/

smoke:
	$(PYTEST) -v tests/test_smoke.py

dataset:
	$(PYTHON) datagen/generate.py --seed 42 --output data/telemetry.jsonl
	$(PYTHON) ingest/load_jsonl.py --input data/telemetry.jsonl --db data/incident.db

calibrate:
	$(PYTHON) eval/calibration.py

eval: calibrate
	$(PYTHON) eval/run_eval.py

demo-replay:
	$(STREAMLIT) run ui/app.py

clean:
	$(PYTHON) -c "import shutil, glob; [shutil.rmtree(p, ignore_errors=True) for p in glob.glob('**/__pycache__', recursive=True)]; [shutil.rmtree(p, ignore_errors=True) for p in ['.pytest_cache', '.ruff_cache']]"
