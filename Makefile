PY ?= .venv/bin/python
AFTERLIFE ?= .venv/bin/afterlife
PYTEST ?= .venv/bin/pytest

.PHONY: install test test-core test-spark lint fetch bronze silver gold export findings \
        conformance pipeline aws-build tf-validate clean

install:
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[spark,local,dbt,aws,dev]" "dbt-spark[session]>=1.11,<2"

# Core and sources only: standard library, no Spark, no network. Under a second.
test-core:
	$(PYTEST) -q -m "not spark"

# Everything, including Spark jobs on synthetic data and, when data/lake
# exists, the Python versus Spark equivalence checks on the real lake.
test:
	$(PYTEST) -q

lint:
	.venv/bin/ruff check afterlife tests aws databricks
	.venv/bin/ruff format --check afterlife tests aws databricks

# The full local pipeline on real public data. Each step writes runs/*.json.
fetch:
	$(AFTERLIFE) fetch retraction-watch --monthly --last-days 14
	$(AFTERLIFE) fetch openalex --budget 0.09
	$(AFTERLIFE) fetch pubmed
	$(AFTERLIFE) fetch pmc-ids

bronze:
	$(AFTERLIFE) bronze

silver:
	$(AFTERLIFE) silver

pmc:
	$(AFTERLIFE) fetch pmc --workers 24
	$(AFTERLIFE) bronze
	$(AFTERLIFE) silver

gold:
	$(AFTERLIFE) gold --target duckdb

export:
	$(AFTERLIFE) export

findings:
	$(AFTERLIFE) findings

conformance:
	$(AFTERLIFE) conformance

pipeline: fetch bronze silver pmc gold export findings

aws-build:
	$(PY) aws/build.py

# Terraform and checkov live in .tools/ (see aws/README.md for how they were
# fetched and checksum verified). checkov has its own venv because installing it
# into .venv would downgrade boto3.
tf-validate:
	cd aws/terraform && ../../.tools/terraform fmt -check -recursive && \
	  ../../.tools/terraform init -backend=false -input=false >/dev/null && \
	  ../../.tools/terraform validate && ../../.tools/terraform test
	.tools/checkov-venv/bin/checkov -d aws/terraform --quiet --compact

clean:
	rm -rf warehouse/target warehouse/logs spark-warehouse metastore_db derby.log aws/build
