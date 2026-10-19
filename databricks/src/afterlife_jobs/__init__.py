"""Lakeflow Job entry points for afterlife.

Packaged into the afterlife wheel (see databricks/README.md for the
pyproject.toml change) so a python_wheel_task can run `afterlife_job`.
The work itself lives in afterlife.spark and afterlife.sources; this package
only adapts it to a job: parameters in, a run report out, an exit status the
scheduler can act on.
"""

from __future__ import annotations

ENTRY_POINT = "afterlife_job"
PACKAGE = "afterlife"
