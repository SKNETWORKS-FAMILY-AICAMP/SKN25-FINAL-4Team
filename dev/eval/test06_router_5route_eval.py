# -*- coding: utf-8 -*-
"""Legacy compatibility wrapper for the current router benchmark.

The current experiment order maps the 5-route router benchmark to
``test09_router_5route_eval``.  Keep this historical filename executable, but
route the implementation through ``router_accuracy_eval.py`` so outputs use the
shared ``experiment-metrics.v1`` schema.
"""

from __future__ import annotations

from router_accuracy_eval import main


if __name__ == "__main__":
    main()
