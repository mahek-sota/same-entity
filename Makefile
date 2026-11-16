PY ?= python3

.PHONY: all tune evaluate site test serve
all: tune evaluate site test

tune:      ## choose thresholds on the dev split only -> config/frozen_rules.json
	$(PY) -m same_entity.tune
evaluate:  ## score dev + held-out with frozen rules -> outputs/
	$(PY) -m same_entity.evaluate
site:      ## outputs/ -> report/index.html
	$(PY) -m same_entity.build_site
test:
	$(PY) -m pytest -q tests
serve:
	$(PY) -m http.server 8000 --directory report --bind 127.0.0.1
