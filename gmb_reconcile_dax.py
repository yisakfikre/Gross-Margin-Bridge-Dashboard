"""
Phase 4 - reconcile ACTUAL Power BI / DAX results against the frozen baseline.

    python3 gmb_reconcile_dax.py <dax_results.csv> [--out report.csv]

Input: a CSV exported from the model (DAX Studio, Tabular Editor, or copied from
a matrix visual) with these columns, header row required:

    test_id,observed

or, if you prefer exporting by coordinates rather than by test_id:

    window_months,filter_channel,filter_category,grain,entity,measure,observed

Blank/empty observed is read as BLANK() and compared against the baseline's
expected-blank rule, NOT as zero. The distinction matters: several tests exist
precisely to catch a measure returning 0 where it must return BLANK.

Exit code 0 = every compared value within tolerance. 1 = at least one failure.
Nothing in this script computes a bridge value; it only compares.
"""
import argparse, hashlib, json, sys
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).resolve().parent
PACK = HERE / "validation_pack"
MANIFEST = PACK / "baseline_manifest.json"

SCHEMA = ["test_id", "window_months", "filter_channel", "filter_category",
          "grain", "entity", "measure", "expected", "kind", "tolerance"]


def _norm_sha256(path):
    """sha256 over newline-normalised bytes.

    Phase 4 defect, closed: the manifest hashes were computed on LF files, but
    a baseline file legitimately edited on Windows comes back CRLF. Hashing raw
    bytes would then fail on a semantically identical file, and a check that
    cries wolf gets switched off - which is how an integrity check stops
    protecting anything. Normalising CRLF/CR to LF keeps the check meaningful
    (it still catches a wrong, truncated, reordered or value-edited file) while
    being platform-agnostic. For an LF file this is identical to plain sha256,
    so it reproduces the frozen manifest values unchanged.
    """
    b = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(b).hexdigest()


def load_baseline():
    """Load the frozen baseline, refusing to proceed unless the pack is intact.

    Phase 4 defect, closed - and the most serious one found this phase. This
    function used to glob baseline_*.csv and concatenate whatever it found,
    with no check on schema, size, or content. When baseline_B5_union_table.csv
    was accidentally overwritten with a raw Q5 DAX export (bracketed headers,
    252 rows, none of the baseline's columns), pd.concat silently unioned those
    rows in with test_id/expected/kind/tolerance/grain all NaN. Every one of
    them then shared an identical all-NaN coordinate key, so drop_duplicates
    collapsed 251 of the 252 away, and the reconciler reported:

        baseline values: 7,414 (after collapsing 503 duplicate coordinates)
          PASS 7,710     NOT_SUPPLIED 1

    A clean-looking pass - while all 210 B5 values were absent from the
    comparison entirely. The tool that exists to catch a broken result reported
    success over a missing test block. That is a false pass, and it is strictly
    worse than the file mix-up that triggered it.

    The pack has always carried baseline_manifest.json with per-file row counts,
    sha256 hashes, and a total. It simply was not consulted. It is now
    mandatory: every manifest file must exist, match its hash and row count, and
    carry the exact baseline schema; no unlisted baseline_*.csv may be present;
    and the concatenated total must equal total_expected_values. Any failure
    aborts before a single value is compared.
    """
    if not MANIFEST.exists():
        sys.exit(f"PACK DEFECT: {MANIFEST.name} is missing from {PACK}. The "
                 f"baseline cannot be verified, so no reconciliation may be "
                 f"treated as evidence. Restore the manifest before running.")
    man = json.loads(MANIFEST.read_text())
    listed = man.get("files", {})
    if not listed:
        sys.exit(f"PACK DEFECT: {MANIFEST.name} lists no baseline files.")

    problems, frames, counts = [], [], {}

    on_disk = {p.name for p in PACK.glob("baseline_*.csv")}
    for extra in sorted(on_disk - set(listed)):
        problems.append(f"  {extra}: present in the pack but not in the "
                        f"manifest. An unlisted baseline file would be "
                        f"concatenated into the comparison unverified.")

    for name, spec in sorted(listed.items()):
        f = PACK / name
        if not f.exists():
            problems.append(f"  {name}: MISSING from {PACK}.")
            continue

        got_hash = _norm_sha256(f)
        want_hash = spec.get("sha256")
        if want_hash and got_hash != want_hash:
            problems.append(f"  {name}: sha256 mismatch.\n"
                            f"      expected {want_hash}\n"
                            f"      found    {got_hash}\n"
                            f"      This file is not the frozen baseline. It has been "
                            f"edited, replaced, or overwritten by another file.")

        try:
            df = pd.read_csv(f)
        except Exception as e:
            problems.append(f"  {name}: unreadable as CSV ({e}).")
            continue

        cols = list(df.columns)
        if cols != SCHEMA:
            missing = [c for c in SCHEMA if c not in cols]
            problems.append(
                f"  {name}: wrong schema.\n"
                f"      expected {SCHEMA}\n"
                f"      found    {cols}\n"
                + (f"      missing  {missing}\n" if missing else "")
                + f"      A file with different column names does not fail loudly on "
                  f"concat - it becomes NaN rows and silently drops its tests.")
            continue

        want_rows = spec.get("rows")
        if want_rows is not None and len(df) != want_rows:
            problems.append(f"  {name}: expected {want_rows:,} rows, found "
                            f"{len(df):,}.")
            continue

        counts[name] = len(df)
        frames.append(df)

    if problems:
        sys.exit("BASELINE INTEGRITY CHECK FAILED - nothing was compared.\n"
                 + "\n".join(problems)
                 + "\n\nRestore the affected file(s) from the frozen pack and re-run. "
                   "A reconciliation over an unverified baseline is not evidence, "
                   "however clean its output looks.")

    b = pd.concat(frames, ignore_index=True)

    want_total = man.get("total_expected_values")
    if want_total is not None and len(b) != want_total:
        sys.exit(f"BASELINE INTEGRITY CHECK FAILED - nothing was compared.\n"
                 f"  manifest total_expected_values = {want_total:,}, "
                 f"concatenated = {len(b):,}.")

    for c in SCHEMA:
        if c in ("expected", "tolerance"):
            continue
        if b[c].isna().any():
            n = int(b[c].isna().sum())
            sys.exit(f"BASELINE INTEGRITY CHECK FAILED - nothing was compared.\n"
                     f"  column '{c}' has {n:,} empty value(s). An empty "
                     f"coordinate collapses into a meaningless key.")
    if b.expected.isna().any():
        sys.exit(f"BASELINE INTEGRITY CHECK FAILED - nothing was compared.\n"
                 f"  {int(b.expected.isna().sum()):,} row(s) have no expected value.")

    print(f"baseline integrity: OK - {len(listed)} files, {len(b):,} values, "
          f"hashes and row counts match {MANIFEST.name}")
    for name in sorted(counts):
        print(f"    {name:<38} {counts[name]:>6,} rows")

    b["entity"] = b.entity.astype(str)
    return b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--out", default="reports/dax_reconciliation.csv")
    a = ap.parse_args()

    base = load_baseline()
    obs = pd.read_csv(a.results, keep_default_na=False, na_values=[""])

    if "test_id" in obs.columns:
        key = ["test_id"]
    else:
        need = ["window_months", "filter_channel", "filter_category", "grain", "entity", "measure"]
        missing = [c for c in need if c not in obs.columns]
        if missing:
            sys.exit(f"results file lacks test_id and is missing {missing}")
        key = need
        obs["entity"] = obs.entity.astype(str)
    if "observed" not in obs.columns:
        sys.exit("results file has no 'observed' column")

    # B1/B2 and B3/B4 intentionally overlap in coordinate space (the same
    # measure at the same coordinates appears in two baseline files). Every
    # such duplicate was verified to carry an identical expected value when
    # the pack was frozen; this re-checks it rather than trusting it, then
    # collapses them so one supplied row satisfies one baseline row.
    collapsed = 0
    if key != ["test_id"]:
        chk = base.groupby(key).expected.nunique()
        conflict = chk[chk > 1]
        if len(conflict):
            sys.exit(f"BASELINE DEFECT: {len(conflict)} coordinate groups carry conflicting "
                     f"expected values. The pack must be re-frozen before reconciling.")
        before = len(base)
        base = base.drop_duplicates(subset=key, keep="first").reset_index(drop=True)
        collapsed = before - len(base)

    m = base.merge(obs[key + ["observed"]], on=key, how="left", indicator=True)

    # Phase 4: the left merge above keeps only baseline rows, so a supplied row
    # whose coordinates appear in no baseline row is dropped without comment.
    # That is the quiet counterpart of NOT_SUPPLIED: it cannot make a wrong
    # value look right, but it does mean an export produced coordinates nothing
    # verifies, which is worth seeing rather than discarding.
    orphan = obs.merge(base[key].drop_duplicates(), on=key, how="left",
                       indicator=True)
    orphan = orphan[orphan._merge == "left_only"]

    m["observed_is_blank"] = m.observed.isna()
    m["delta"] = np.where(m.observed_is_blank, np.nan, m.observed - m.expected)
    m["abs_delta"] = m.delta.abs()

    def verdict(r):
        # BLANK is read as "no value". For a count it is always wrong. For a
        # money or ratio measure it is compared as 0.0 against the same
        # tolerance as any other observation: if the expected value is itself
        # indistinguishable from zero at that tolerance (a residual, a zeroed
        # lifecycle component), BLANK and 0 are both correct answers and the
        # test passes. Anywhere else BLANK fails and is labelled so.
        if r["_merge"] == "left_only":
            return "NOT_SUPPLIED"
        if r["observed_is_blank"]:
            if r["kind"] == "count":
                return "FAIL_BLANK"
            return "PASS" if abs(r["expected"]) <= r["tolerance"] else "FAIL_BLANK"
        if r["kind"] == "count":
            return "PASS" if float(r["observed"]) == float(r["expected"]) else "FAIL"
        return "PASS" if r["abs_delta"] <= r["tolerance"] else "FAIL"

    m["status"] = m.apply(verdict, axis=1)
    m["tolerance_multiple"] = np.where(m.tolerance > 0, m.abs_delta / m.tolerance, np.nan)

    cols = ["test_id", "window_months", "filter_channel", "filter_category", "grain",
            "entity", "measure", "kind", "expected", "observed", "delta", "abs_delta",
            "tolerance", "tolerance_multiple", "status"]
    out = m[cols].sort_values(
        ["status", "abs_delta"], ascending=[True, False], na_position="last")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out, index=False)

    vc = out.status.value_counts().to_dict()
    print(f"baseline values: {len(base):,}"
          + (f" (after collapsing {collapsed} duplicate coordinates)" if collapsed else "")
          + f"   supplied: {int((out.status != 'NOT_SUPPLIED').sum()):,}")
    for k in ("PASS", "FAIL", "FAIL_BLANK", "NOT_SUPPLIED"):
        if vc.get(k):
            print(f"  {k:<13} {vc[k]:>6,}")

    if len(orphan):
        print(f"  {'UNVERIFIED':<13} {len(orphan):>6,}  supplied row(s) whose "
              f"coordinates match no baseline row")
        shown = orphan[key].drop_duplicates().head(10)
        for _, r in shown.iterrows():
            print("      " + " | ".join(f"{c}={r[c]}" for c in key))
        if len(orphan[key].drop_duplicates()) > len(shown):
            print(f"      ... and {len(orphan[key].drop_duplicates()) - len(shown):,} "
                  f"more distinct coordinate(s)")
    else:
        print(f"  {'(no orphans)':<13} {'':>6}  every supplied row matched a "
              f"baseline coordinate")
    bad = out[out.status.str.startswith("FAIL")]
    if len(bad):
        print(f"\nworst 20 differences:")
        for _, r in bad.head(20).iterrows():
            o = "BLANK" if pd.isna(r.observed) else f"{r.observed:,.6f}"
            print(f"  {r.status:<10} {str(r.test_id)[:52]:<52} expected {r.expected:>16,.6f}  "
                  f"observed {o:>18}  delta {r.delta if pd.notna(r.delta) else float('nan'):>14,.6f}  "
                  f"= {r.tolerance_multiple:,.1f}x tolerance" if pd.notna(r.tolerance_multiple)
                  else f"  {r.status:<10} {str(r.test_id)[:52]:<52} expected {r.expected:>16,.6f}  observed {o}")
    print(f"\nwritten: {a.out}")
    sys.exit(1 if len(bad) else 0)


if __name__ == "__main__":
    main()
