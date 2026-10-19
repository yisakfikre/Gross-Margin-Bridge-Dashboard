# Metric definitions

Every metric on the dashboard, with its formula, unit, sign convention, grain and known limitations. DAX implementations are in [`../powerbi/measures.dax`](../powerbi/measures.dax).

---

## 1. Comparison basis

**The current period (CP) is the most recent N months. The prior period (PP) is the same N months one year earlier.** Not a rolling window, not the immediately preceding N months.

N is set by the slicer — 12, 6 or 3 — through a disconnected `dim_period` table:

```dax
Selected Months = SELECTEDVALUE ( dim_period[months], 12 )
```

Period membership is driven by `dim_date[months_from_cp_end]`, where 0 is the most recent month:

| Period | Condition |
| --- | --- |
| CP | `0 ≤ months_from_cp_end < N` |
| PP | `12 ≤ months_from_cp_end < 12 + N` |

---

## 2. Base measures

| Measure | Formula | Unit |
| --- | --- | --- |
| `Net Revenue` | `SUM(fact_sales_line[net_revenue])` | USD |
| `Total Cost` | `SUM(fact_sales_line[total_cost])` | USD |
| `Gross Profit` | `[Net Revenue] − [Total Cost]` | USD |
| `Gross Margin %` | `DIVIDE([Gross Profit], [Net Revenue])` | ratio |

`net_revenue = gross_revenue − discount_amount`, and `total_cost = quantity × unit_standard_cost`. Both identities are asserted row-by-row in `gmb_audit.py`.

**Gross margin is a ratio, never summed.** It is recomputed at every grain from its own numerator and denominator.

---

## 3. Movement measures

| Measure | Formula | Unit | Sign |
| --- | --- | --- | --- |
| `Net Revenue Delta` | `[Net Revenue CP] − [Net Revenue PP]` | USD | negative = adverse |
| `Gross Profit Delta` | `[Gross Profit CP] − [Gross Profit PP]` | USD | negative = adverse |
| `Gross Margin Delta bps` | `(GM CP − GM PP) × 10000` | basis points | negative = adverse |

**Negative is always adverse, everywhere on the page.** Adverse renders coral, favourable teal, totals neutral grey.

**Value and percentage are kept distinct.** A −$536.0K profit movement and a −324.9 bps margin movement are different facts and never appear in the same unit.

---

## 4. The bridge

### 4.1 Method

A **midpoint (Bennet) index decomposition**, computed per product over the comparable population and summed.

For each comparable product, with prior-period price/quantity/cost `p₀, q₀, c₀` and current `p₁, q₁, c₁`:

| Component | Per-product contribution |
| --- | --- |
| **Price** | `(p₁ − p₀) × (q₀ + q₁) / 2` |
| **Cost** | `−(c₁ − c₀) × (q₀ + q₁) / 2` |
| **Volume** | `γ × (p̄ − c̄) × q̄` summed, where `γ` is the volume index |
| **Mix** | `(q₁ − q₀ − γ·q₀) × (p̄ − c̄)` |

with `p̄ = (p₀+p₁)/2`, `c̄ = (c₀+c₁)/2`, `q̄ = (q₀+q₁)/2`.

The volume index is value-weighted, not unit-weighted:

```
γ = Σ p̄ · (q₁ − q₀)  /  Σ p̄ · q̄
```

**Verified value: γ = 0.069241332.**

### 4.2 Why midpoint

**It is symmetric.** Reverse the two periods and every component negates exactly — tested in `final_gate_run7.csv` R3. A Laspeyres or Paasche decomposition does not have this property, and the choice of base period silently changes the answer.

**It allocates interaction effects** between the components rather than leaving a residual term for the reader to interpret.

### 4.3 Product entry and exit

The 300 SKUs partition into three **disjoint and exhaustive** populations — verified R4:

| Population | Count | Treatment |
| --- | --- | --- |
| **Comparable** — traded in both periods with `q > 0` | 255 | Enters price, cost, volume and mix |
| **New** — current period only | 25 | Its entire gross profit becomes the New Products effect |
| **Discontinued** — prior period only | 20 | Its entire prior gross profit becomes the Discontinued effect, negated |

**New and discontinued products are excluded from the volume index.** Including them would make γ measure the arrival of products rather than a change in trading volume. Verified: removing the new SKUs moves only the New step, with maximum movement elsewhere of $0.00.

**No double counting.** Each product contributes to exactly one population.

### 4.4 Reconciliation

```
Volume + Mix + Price + Cost + New + Discontinued = Gross Profit Delta
```

**Tolerance:** `max($1.00, 1e-9 × |Gross Profit PP|)`. Observed residual on the full population: **$0.00**. Worst across 168 selections: **$1.75 × 10⁻⁹**.

The reconciliation indicator on the dashboard is computed from the actual residual, not hardcoded.

### 4.5 Selection relativity — the most important caveat

**Gross Profit Delta is exactly additive at every grain.** Channel totals sum to −$535,997.76 to the cent.

**The decomposed effects are not.** Filter to a channel and γ is recomputed for that channel's product mix, so the result is *that channel's own decomposition*, not its contribution to the total. The channel Price Effects sum to −$459,014 against a total of −$442,406 — a **−$16,609** gap.

This is a property of the method, not a defect, and it is disclosed on the heatmap. **Never state a total for a decomposed effect by summing per-entity values.**

---

## 5. Price sub-decomposition

| Measure | Definition | Verified |
| --- | --- | --- |
| `List Price Sub-effect` | Price effect using list prices rather than realised prices | **+$309,982.86** |
| `Discount Sub-effect` | `[Price Effect] − [List Price Sub-effect]` | **−$752,388.41** |
| `Price Effect` | Their sum | **−$442,405.55** |

**Interpretation limit.** This shows that realised prices fell while list prices rose. It does **not** establish discount creep — the model records discount amounts, not approvals, contract terms or negotiation history.

---

## 6. Investigation list

An account is flagged if it appears in **either** ranking:

- the 12 largest adverse `Gross Profit Delta` values, **or**
- the 12 largest adverse `Price Effect` values

Only accounts with an adverse movement on at least one measure are eligible, so a favourable account can never be ranked in.

**Verified result:** 13 accounts of 400, totalling **−$702.2K** gross-profit movement and **$5,878.7K** current-period revenue.

**A union of two Top-12 rankings yields between 12 and 24 rows.** Thirteen means the rankings overlap heavily — eleven accounts appear in both.

**Two traps this avoids:**

**Percentage ranking.** Ranking by margin change percentage lets an account with $400 of revenue outrank one with $2.2m. The ranking is on absolute movement; revenue is displayed so materiality is visible.

**Summing the Total row.** The table's Price Effect total reads **−$655,076**, not the −$523,101 you get by adding the thirteen rows. Power BI re-evaluates the measure over the group, which for a decomposed effect is the correct figure — see §4.5.

---

## 7. Single-month event panel

Fixed to **September 2025 → October 2025**, the largest single-month margin step in the window: **−342 bps**. It does not respond to the period slicer, and says so.

| Measure | Definition | Unit |
| --- | --- | --- |
| `Oct Discount Rate Change pts` | Change in realised discount rate, Sep → Oct | percentage points |
| `Oct Margin Contribution bps` | That account's contribution to the −342 bps step | basis points |
| `Oct Net Revenue` | October net revenue | USD |

Accounts above **+5 points** of discount-rate increase are highlighted: **12 accounts, 15.6% of revenue, −215 bps**.

**The annotation reports basis points, not a percentage of the decline.** A share-of-decline ratio has a denominator that nets positive against negative contributions and collapses toward zero — under one category filter it reaches 1,450%. Basis points are absolute and additive and cannot explode. The percentage clause appears only where it is well-behaved.

---

## 8. Metrics evaluated and rejected

**Benchmark Margin Opportunity.** A "gross profit gap to peer benchmark" KPI was specified and then **excluded**. The peer population could not be defined defensibly on this data, and any figure would have implied recoverable profit that the analysis cannot support.

**Price-versus-volume quadrant.** Evaluated and dropped. It could not distinguish causal price elasticity from observed association, and the space was better spent on the account-level concentration finding.

---

## 9. Limitations that apply to every metric here

1. **Standard costs, not actual.** 250 of 300 SKUs had costs revised at the financial-year boundary. A costing-basis change and real supplier inflation are indistinguishable.
2. **Mathematical attribution, not causation.** The bridge identifies which component moved, never why.
3. **No reason codes.** Amounts are recorded; approvals, contracts and negotiations are not.
4. **One seasonal cycle.** 24 months contains a single September-to-October transition.
5. **Synthetic data.** No figure describes a real business.
