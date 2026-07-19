# Capture reference

Capture quality metrics (`tools/capture_quality_report.py`):

- WACZ count, capture-JSON count
- DOM event count, snapshot count
- rrweb coverage, snapdom coverage
- template generation success rate (gate-ready candidates / yield)

Artifacts live on the operator host; in a clean tree only yield-based metrics are populated.
