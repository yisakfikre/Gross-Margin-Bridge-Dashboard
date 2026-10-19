# Validation report

**Dataset:** `run7-accepted`, seed `20261010` · **Baseline frozen:** 11 October 2026 · **Dashboard frozen:** 18 October 2026

Every figure the dashboard displays reconciles to a baseline computed independently of the Power BI implementation. This document states what was checked, how, and what remains outside the evidence.

---

## 1. Why there are two implementations

The bridge is computed twice, by separate code paths that do not share logic:

| Implementation | Where | Role |
| --- | --- | --- |
| **Python** | `gmb_bridge.py` | The reference. Computes the midpoint index decomposition directly from the CSVs |
| **DAX** | `powerbi/measures.dax` | The production implementation inside the report |

A reference calculation that merely re-expressed the DAX would prove nothing. These were written to the same mathematical definition, not to each other, and the reconciliation compares their outputs.

`gmb_bridge.py` is hash-pinned in `validation_pack/baseline_manifest.json`
(`sha256 82ac708d7523e508071e5337f97f9d4e28cb00a4fcc6a7d0e6488b1e038e14dd`), so any change to the reference invalidates the baseline rather than silently moving it.

---

## 2. Tolerances — set before testing, not after

| Quantity | Tolerance |
| --- | --- |
| Money | `max($1.00, 1e-9 × abs(Gross Profit PP))` for the selection |
| Ratios | `1e-9` absolute, applied to the ratio, not the percentage |
| Counts | Exact |

Computation runs in IEEE 754 double precision with no intermediate rounding. Three-decimal `$m` display values are **forbidden as test inputs** — at a $1.00 tolerance they carry up to $500 of rounding error.

---

## 3. The frozen baseline — 7,875 values

| File | Rows | Covers |
| --- | --- | --- |
| `baseline_B1_totals.csv` | 60 | Period totals and every bridge component at 12 / 6 / 3-month windows |
| `baseline_B2_selection_matrix.csv` | 756 | Every channel × category × window combination |
| `baseline_B3_grains.csv` | 6,624 | Product, category, customer and channel grain |
| `baseline_B4_lifecycle.csv` | 225 | Comparable, new and discontinued populations |
| `baseline_B5_union_table.csv` | 210 | The investigation-list union rule |

Each file carries a SHA-256 in the manifest, as do all four source CSVs.

---

## 4. Reconciliation result

Both harnesses below were **re-run against the published files on 19 October 2026**.

| Harness | Checks | Result |
| --- | --- | --- |
| `gmb_audit.py run7-accepted` | 87 | **81 pass · 2 fail · 4 reported**. Both failures are designed-scenario checks (§5.1). No data-integrity check failed |
| `gmb_final_gate.py` | 40 | **37 pass · 0 fail · 3 reported without threshold** |

| Check | Result |
| --- | --- |
| Bridge residual, full population | **$0.00** |
| Bridge residual, worst of 168 category × channel selections | **$1.75 × 10⁻⁹** |
| Volume index γ | **6.924%** |
| Six components sum to the actual gross-profit change | Exact |

---

## 5. Property tests — `reports/final_gate_run7.csv`

**40 checks: 37 pass, 0 fail, 3 reported without threshold.**

These test mathematical properties the decomposition must satisfy, independently of any particular number being right.

| Ref | Property | Result |
| --- | --- | --- |
| **R1** | Reproduces the hand-worked example exactly (γ 8.2759%, volume +191.17, mix −71.17, price −100.00) | PASS |
| **R1** | Negative control still returns +156.5217 — no unit-weighted index has crept in | PASS |
| **R2** | Bridge reconciles across 168 selections and on the full population | PASS |
| **R3** | **Symmetry.** Reverse the periods and every component negates: volume +0.6286 → −0.6286, mix −0.1698 → +0.1698, γ 0.069241 → −0.069241 | PASS |
| **R3** | New and Discontinued exchange under reversal: new +0.0942 vs disc′ −0.0942 | PASS |
| **R4** | Populations disjoint and exhaustive: **255 comparable + 25 new + 20 discontinued = 300** | PASS |
| **R4** | Measured populations match the `dim_product` lifecycle flags exactly | PASS |
| **R4** | Removing the new SKUs moves **only** the New step — maximum movement elsewhere $0.00 | PASS |
| **R4** | New and discontinued SKUs are excluded from the volume index — γ unchanged at 0.06924133 | PASS |
| **R5** | Price, cost, new, discontinued and ΔGP are additive across the category partition | PASS |
| **R5** | Mix is additive **at a fixed index** | PASS |
| **R5** | Mix is **not** additive at a recomputed index — a documented property, which must differ: −$0.0459m vs −$0.1698m, gap $0.1240m | PASS |

**R5's last row is the one worth understanding.** It is a test that something *fails* to be additive, because the volume index is recomputed per selection. Treating the decomposed effects as if they summed across filters would be wrong, and the dashboard discloses this on the heatmap.

---

## 6. Verified headline figures

Recomputed from `validation/baseline_B1_totals.csv`:

| Measure | Value |
| --- | --- |
| Net Revenue PP → CP | 32,382,903.62 → 34,384,318.77 |
| Gross Profit PP → CP | 9,401,950.78 → 8,865,953.02 |
| **Gross Profit Δ** | **−535,997.76** |
| Gross Margin PP → CP | 29.0336867% → 25.7848733% (**−324.88 bps**) |
| Volume Effect | +628,587.45 |
| Mix Effect | −169,824.78 |
| Price Effect | −442,405.55 |
| Cost Effect | −595,085.31 |
| New Products Effect | +94,247.79 |
| Discontinued Effect | −51,517.37 |
| List Price Sub-effect | +309,982.86 |
| Discount Sub-effect | −752,388.41 |
| Volume Index γ | 0.069241332 |
| **Bridge Residual** | **−0.00** |

---

## 7. Defects found and fixed during the build

Recorded because a validation report that lists only passes is not a validation report.

### 7.1 The two open audit failures

| Check | Target | Observed |
| --- | --- | --- |
| Concentration carried by the top 12 accounts | 60% ± 10pp | **46.5%** |
| The 12 worst accounts are the designed pressure group | 12 of 12 | **11 of 12** |

These test whether the generator produced the *intended* demonstration pattern. It produced a
weaker version. **They were left failing rather than re-thresholded to pass** — the designed
hypothesis and the measured outcome are different things, and a test moved to fit its data is no
longer a test.

### 7.2 Defects found and fixed

| Defect | Cause | Resolution |
| --- | --- | --- |
| Waterfall rendered a `(Blank)` 9.4M bar | A rejected design was built from a document that still carried live instructions | Anchor removed; six-component bridge restored |
| Tooltip showed a −$1,164,585 residual on a reconciling bridge | The hovered bar filters `step_label` while the effect measures filter `step` — different columns | Residual removed from the tooltip well |
| Investigation list returned 219 rows instead of 13 | `ALL(customer_id)` removed the visual's grouping on `customer_name` and `channel` | Changed to `ALLSELECTED(dim_customer)` |
| Margin-trajectory insight reported a 0 bps spread | `ALL(dim_date)` inside `CALCULATE` destroyed the month being iterated | Filter argument removed; true spread 58 bps |
| A proposed "% of the decline" annotation reached 1,450% under one filter | Two-sided ratio with a denominator that collapses toward zero | Quantity reported in bps; percentage shown only when well-behaved |

**Four of the five share one cause:** `ALL()` inside `CALCULATE` removing filter context the calculation still needed. Any measure that iterates a table and calls `CALCULATE` inside the iterator is now checked for it. A zero range, a zero spread or an identical pair of values is treated as a collapsed filter context, not a finding.

---

## 8. What this validation does not cover

Stated explicitly, because the boundary matters more than the passes.

- **It validates arithmetic, not business truth.** Every number is correctly computed from the data. Whether the data describes anything real is a separate question, and here it does not — the dataset is synthetic.
- **It cannot validate causation.** No test can establish that discounting caused the margin decline. The data records amounts, not reasons.
- **Standard costs are taken as given.** The cost effect is correctly computed from the costs in the model. Whether those costs reflect supplier reality is outside the data.
- **Report-level interaction behaviour** — tooltip configuration, cross-filter settings, conditional-formatting bindings — is verified by inspection in Power BI Desktop, not by the automated harness, which operates on the data model rather than the report layer.

---

## 9. Reproducing this

Run from the repository root — every script resolves `data/` and `reports/` relative to its own location.

```bash
python gmb_audit.py run7-accepted   # 87 checks; prints the bridge at the end
python gmb_final_gate.py            # 40 property checks
python gmb_baseline_freeze.py       # rebuilds validation_pack/ from the data
```

`gmb_reconcile_dax.py` takes a DAX export from Power BI as its argument
(`python gmb_reconcile_dax.py <export.csv>`). **That export is not published here**, so this
script cannot be run from the repository alone.

Environment the baseline was frozen under: Python 3.13.16, numpy 2.5.3, pandas 3.0.5. Minor version differences should not move results at these tolerances; if they do, that is itself a finding.
