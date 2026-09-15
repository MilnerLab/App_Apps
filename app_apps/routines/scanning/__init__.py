"""Reusable pieces of a measurement scan.

Three layers, deliberately separable:

* :mod:`patterns` — *what kind of scan*. Pure functions over numbers: where the points
  go and how far apart. No hardware, no threads, no bus.
* :mod:`translation_stage` — *one stage, moved synchronously*. Validates against the
  stage's own soft limits and blocks until motion is genuinely complete.
* :mod:`translation_stage_scan` — *walk a stage through a list of positions*, one point
  per iteration, handing control back to the caller at each.

None of these knows what a pause is. Run control belongs to the routine that owns them;
see ``BaseRoutine`` / ``RunControl`` in Base_Core.
"""
