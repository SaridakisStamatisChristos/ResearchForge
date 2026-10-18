# Toy optimizer — built-in real offline fixture

Run `rf demo run --offline --output /tmp/rf-demo-1`.

The package-generated toy source repository contains three **real Git commits**
holding a `model.json` cutoff parameter. Its independent evaluator script
computes mean absolute prediction error on frozen, disjoint development
(x=1,2), qualification (x=5,6), and untouched final (x=8,9) samples.

- Baseline model (cutoff 0): underfits all partitions.
- Good model (cutoff 100): improves development and qualification, and the
  sealed final evaluation later confirms the declared aggregate threshold.
- Overfit model (cutoff 2): perfect development but no qualification gain.
  It is rejected **before** the final data is accessed.

All measurement values are computed by real Python subprocesses against actual
Git worktrees; no metric is written directly into the evidence ledger.
`summary.json`, `report.json`, `report.md`, `campaign/manifest.lock.json`,
and the event/CAS directory are retained under the exclusive output path.
