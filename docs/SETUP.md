# Setup guide

Three ways to use this repository, from zero effort to full reproduction.

---

## Option 1 — Just read it

Nothing to install. The dashboard image in the README and the documents in this folder contain the complete analysis.

- **[Full-resolution dashboard](../images/dashboard-full.png)** — 3840 × 2160
- **[Validation report](VALIDATION.md)** — what was checked and how
- **[Metric definitions](METRIC_DEFINITIONS.md)** — every formula and sign convention
- **[Data dictionary](DATA_DICTIONARY.md)** — every field and constraint

---

## Option 2 — Open the report

**Requires:** Power BI Desktop (free, Windows only) — [powerbi.microsoft.com/desktop](https://powerbi.microsoft.com/desktop/)

1. Download `powerbi/Gross-Margin-Bridge.pbix`
2. Open it in Power BI Desktop
3. The data is embedded — **no refresh, no data source, no credentials needed**

If you want to repoint it at the CSVs anyway:

1. **Home → Transform data → Data source settings**
2. **Change Source** on each of the four queries
3. Point each at the matching file in `data/`
4. **Close & Apply**

### Using the dashboard

| Control | Effect |
| --- | --- |
| **12 Mo / 6 Mo / 3 Mo** | Comparison window. Always compares against the same period one year earlier |
| **Channel / Category** | Scope every visual |
| **Clear Slicer** | Reset to the default view |
| **Clicking a heatmap cell** | Filters the rest of the page to that category × channel |

**Two visuals deliberately ignore the period slicer** and say so in their subtitles: the margin trend always shows 24 months, and the October panel is a fixed September-to-October comparison.

**One thing to watch.** Filtering the bridge to a single channel shows *that channel's own decomposition*, not its contribution to the company total. Gross-profit change is additive across selections; the decomposed effects are not, because the volume index is recomputed. The heatmap states this.

---

## Option 3 — Reproduce the numbers

**Requires:** Python 3.11 or later.

```bash
git clone https://github.com/yisakfikre/Gross-Margin-Bridge-Report.git
cd Gross-Margin-Bridge-Report

python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install pandas numpy
```

### Verify the published figures

**Run every script from the repository root.** They resolve `data/` and `reports/` relative to
their own location, so running them from elsewhere will fail to find the data.

```bash
python gmb_audit.py run7-accepted
```
87 checks — key uniqueness, foreign keys, arithmetic identities, sign conventions, and the
designed-scenario patterns. Expect **81 pass, 2 fail, 4 reported**. The two failures are the
documented designed-scenario checks; see [VALIDATION.md §7.1](VALIDATION.md). It finishes by
printing the bridge:

```
BRIDGE  dGP -0.536m = volume +0.629 | mix -0.170 | price -0.442 | cost -0.595 | new +0.094 | disc -0.052   gamma 6.924%  residual $-0.000000
```

```bash
python gmb_final_gate.py
```
The property suite — symmetry, population disjointness, additivity. Expect
**40 checks: 37 pass, 0 fail, 3 reported without threshold**. Writes `reports/final_gate_run7.csv`.

```bash
python gmb_baseline_freeze.py
```
Rebuilds `validation_pack/` — 7,875 expected values plus the manifest.

`gmb_reconcile_dax.py` compares a DAX export against that baseline and takes the export as its
argument. The export comes from Power BI and is **not** published here, so this script cannot be
run from the repository alone.

### Regenerate the dataset from scratch

```bash
python gmb_generate.py --seed 20261010 --run-label run7-accepted
```

The seed is what makes this reproducible. **Change it and every figure in the README becomes wrong** — the baseline is pinned to `run7-accepted` by SHA-256 in `validation_pack/baseline_manifest.json`, and a regenerated dataset will not match those hashes.

Baseline environment: Python 3.13.16, numpy 2.5.3, pandas 3.0.5.

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Hash mismatch after regenerating | Different seed, or a different pandas/numpy version. Use the committed CSVs to verify the published numbers |
| Power BI asks for credentials | The embedded data was dropped. Repoint the sources as above — the CSVs need no authentication |
| Scripts can't find the data | You are not in the repository root. `cd` there first — paths are resolved relative to each script |
| Bridge shows an unexpected total | Check whether a slicer is active. Filtered totals are correct but are *that selection's* decomposition |
