"""F0 before/after audit. Run from the repository root.

Compares a freshly rebuilt extraction run against the committed before-snapshot,
and checks the six confirmed defects are gone.

Usage:  python f0_audit.py <path-to-new-run-dir>
"""
import json
import sys
from pathlib import Path

SNAP = Path(__file__).parent / "f0_before"

DEFECTS = [
    ("market_count", "2021-03-31", 44.0),
    ("market_count", "2022-03-31", 53.0),
    ("market_count", "2023-03-31", 50.0),
    ("market_count", "2023-12-31", 53.0),
    ("pct_homes_on_market_gt_120_days", "2023-12-31", 55.0),
    ("housing_inventory_homes", "2023-12-31", 12788.0),
]

# The value that must survive in each of those six groups.
EXPECTED_SURVIVORS = [
    ("market_count", "2021-03-31", 27.0),
    ("market_count", "2022-03-31", 45.0),
    ("market_count", "2023-03-31", 53.0),
    ("market_count", "2023-12-31", 50.0),
    ("pct_homes_on_market_gt_120_days", "2023-12-31", 18.0),
    ("housing_inventory_homes", "2023-12-31", 5326.0),
]


def load(run: Path):
    return [json.loads(l) for l in (run / "observations.jsonl").open()]


def main(run_dir: str) -> int:
    run = Path(run_dir)
    after = load(run)
    before_ids = set((SNAP / "observation_ids.txt").read_text().split("\n"))
    defect_ids = set((SNAP / "defect_ids.txt").read_text().split("\n"))
    after_ids = {o["observation_id"] for o in after}

    print(f"observations before : {len(before_ids)}")
    print(f"observations after  : {len(after)}  (distinct ids {len(after_ids)})")

    gone = before_ids - after_ids
    new = after_ids - before_ids
    print(f"\nids that disappeared : {len(gone)}")
    print(f"ids that appeared    : {len(new)}")

    failures = []

    print("\n--- the six confirmed defects ---")
    for metric, inst, value in DEFECTS:
        hit = [o for o in after
               if o["metric_id"] == metric and o["instant_date"] == inst
               and o["value"] == value]
        state = "STILL PRESENT  <-- FAIL" if hit else "gone"
        if hit:
            failures.append(f"{metric}@{inst}={value} still present")
        print(f"  {metric:<34} {inst}  {value:<10} {state}")

    print("\n--- the value that must survive in each group ---")
    for metric, inst, value in EXPECTED_SURVIVORS:
        hit = [o for o in after
               if o["metric_id"] == metric and o["instant_date"] == inst
               and o["value"] == value]
        state = f"present ({len(hit)})" if hit else "MISSING  <-- FAIL"
        if not hit:
            failures.append(f"{metric}@{inst}={value} missing")
        print(f"  {metric:<34} {inst}  {value:<10} {state}")

    print("\n--- ids that disappeared but were NOT among the six defects ---")
    unexpected_gone = sorted(gone - defect_ids)
    for oid in unexpected_gone:
        print(f"  {oid}")
    if not unexpected_gone:
        print("  (none)")

    print("\n--- ids that appeared (expected: redated table readings) ---")
    for oid in sorted(new):
        o = next(x for x in after if x["observation_id"] == oid)
        per = o["instant_date"] or f"{o['period_start']}..{o['period_end']}"
        print(f"  {oid}\n      {o['metric_id']} {per} = {o['value']} "
              f"({o['passage_id'].split('#')[-1]})")
    if not new:
        print("  (none)")

    stable = before_ids & after_ids
    print(f"\nids stable across the change: {len(stable)}")

    print("\n=== VERDICT ===")
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("  all six defects gone; all six survivors present")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else
                  "data/extraction_runs/extract-v1-lexical-2422c4252c07"))
