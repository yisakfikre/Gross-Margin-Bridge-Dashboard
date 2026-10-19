"""
Phase 4 - freeze the acceptance baseline at full precision.

Emits validation_pack/baseline_*.csv plus a manifest with hashes.
Every value is computed to full float precision from run7-accepted and written
with 6 decimal places. Display rounding is a presentation concern only and is
NEVER a test input - see ROUNDING CONVENTION below.

ROUNDING CONVENTION (frozen, Phase 4)
  - Computation: IEEE 754 double throughout. No intermediate rounding anywhere
    in gmb_bridge.py.
  - Storage in this pack: 6 decimal places of a USD amount, 10 of a ratio.
  - Comparison: absolute difference against tolerance
        money  |observed - expected| <= max(1.00, 1e-9 * |Gross Profit PP|)
        gamma  |observed - expected| <= 1e-9 (ratio, not percent)
        counts exact equality
  - Display: $m to 3 decimals is for reading only. A 3-decimal $m value carries
    +/- $500 of error, which is 500x the money tolerance. Using one as a test
    input produces false failures. This happened once in Phase 4 and is the
    reason this file exists.
"""
import hashlib, json, sys
from pathlib import Path
import numpy as np, pandas as pd
from gmb_bridge import bridge, price_subsplit, lines_to_sku

HERE = Path(__file__).resolve().parent
OUT = HERE / "validation_pack"
OUT.mkdir(exist_ok=True)

fact = pd.read_csv("data/fact_sales_line.csv")
prod = pd.read_csv("data/dim_product.csv")
cust = pd.read_csv("data/dim_customer.csv")
date = pd.read_csv("data/dim_date.csv")
F = fact.merge(prod[["product_id", "category", "sub_category", "lifecycle"]], on="product_id") \
        .merge(cust[["customer_id", "channel", "segment"]], on="customer_id") \
        .merge(date[["date", "months_from_cp_end"]], left_on="invoice_date", right_on="date")


def window(w):
    if w == 12:
        return F
    cur = F[(F.months_from_cp_end >= 0) & (F.months_from_cp_end < w)].assign(period="CP")
    pri = F[(F.months_from_cp_end >= 12) & (F.months_from_cp_end < 12 + w)].assign(period="PP")
    return pd.concat([cur, pri])


def row(**kw):
    return kw


# ---------------------------------------------------------------- B1 totals
b1 = []
for w in (12, 6, 3):
    g = window(w)
    s = lines_to_sku(g)
    b = bridge(s)
    ss = price_subsplit(s)
    r0 = g.loc[g.period == "PP", "net_revenue"].sum()
    r1 = g.loc[g.period == "CP", "net_revenue"].sum()
    for m, v in (("Net Revenue PP", r0), ("Net Revenue CP", r1),
                 ("Gross Profit PP", b["gp0"]), ("Gross Profit CP", b["gp1"]),
                 ("Gross Profit Delta", b["dgp"]),
                 ("Gross Margin PP", b["gp0"] / r0), ("Gross Margin CP", b["gp1"] / r1),
                 ("Price Effect", b["price"]), ("Cost Effect", b["cost"]),
                 ("Volume Effect", b["volume"]), ("Mix Effect", b["mix"]),
                 ("New Products Effect", b["new"]), ("Discontinued Effect", b["disc"]),
                 ("List Price Sub-effect", ss["list_effect"]),
                 ("Discount Sub-effect", ss["discount_effect"]),
                 ("Volume Index Gamma", b["gamma"]), ("Bridge Residual", b["residual"])):
        kind = "ratio" if ("Margin" in m or "Gamma" in m) else "money"
        b1.append(row(test_id=f"B1-{w}m-{m.replace(' ','_')}", window_months=w,
                      filter_channel="(all)", filter_category="(all)", grain="total",
                      entity="(total)", measure=m, expected=round(float(v), 10),
                      kind=kind, tolerance=1e-9 if kind == "ratio" else max(1.0, 1e-9 * abs(b["gp0"]))))
    b1.append(row(test_id=f"B1-{w}m-Comparable_SKUs", window_months=w, filter_channel="(all)",
                  filter_category="(all)", grain="total", entity="(total)",
                  measure="Comparable SKU Count", expected=float(b["n_comp"]), kind="count", tolerance=0))
    b1.append(row(test_id=f"B1-{w}m-New_SKUs", window_months=w, filter_channel="(all)",
                  filter_category="(all)", grain="total", entity="(total)",
                  measure="New SKU Count", expected=float(b["n_new"]), kind="count", tolerance=0))
    b1.append(row(test_id=f"B1-{w}m-Discontinued_SKUs", window_months=w, filter_channel="(all)",
                  filter_category="(all)", grain="total", entity="(total)",
                  measure="Discontinued SKU Count", expected=float(b["n_exit"]), kind="count", tolerance=0))
pd.DataFrame(b1).to_csv(OUT / "baseline_B1_totals.csv", index=False)

# ------------------------------------------------- B2 the 84-selection matrix
b2 = []
cats = sorted(F.category.unique())
chans = sorted(F.channel.unique())
for w in (12, 6, 3):
    base = window(w)
    for ch in ["(all)"] + chans:
        for ca in ["(all)"] + cats:
            g = base
            if ch != "(all)":
                g = g[g.channel == ch]
            if ca != "(all)":
                g = g[g.category == ca]
            if g.empty:
                continue
            b = bridge(lines_to_sku(g))
            tolm = max(1.0, 1e-9 * abs(b["gp0"]))
            for m, v, k in (("Gross Profit Delta", b["dgp"], "money"),
                            ("Price Effect", b["price"], "money"),
                            ("Cost Effect", b["cost"], "money"),
                            ("Volume Effect", b["volume"], "money"),
                            ("Mix Effect", b["mix"], "money"),
                            ("New Products Effect", b["new"], "money"),
                            ("Discontinued Effect", b["disc"], "money"),
                            ("Volume Index Gamma", b["gamma"], "ratio"),
                            ("Bridge Residual", b["residual"], "money")):
                b2.append(row(test_id=f"B2-{w}m-{ch}-{ca}-{m.replace(' ','_')}".replace(" ", "_"),
                              window_months=w, filter_channel=ch, filter_category=ca,
                              grain="total", entity="(total)", measure=m,
                              expected=round(float(v), 10), kind=k,
                              tolerance=1e-9 if k == "ratio" else tolm))
pd.DataFrame(b2).to_csv(OUT / "baseline_B2_selection_matrix.csv", index=False)

# ------------------------------------------------------------ B3 five grains
b3 = []
grains = (("category", "category"), ("sub_category", "sub_category"), ("product", "product_id"),
          ("channel", "channel"), ("segment", "segment"), ("account", "customer_id"))
for gname, col in grains:
    for key, g in F.groupby(col):
        s = lines_to_sku(g)
        b = bridge(s)
        ss = price_subsplit(s) if ((s.q0 > 0) & (s.q1 > 0)).any() else {"list_effect": 0.0, "discount_effect": 0.0}
        tolm = max(1.0, 1e-9 * abs(b["gp0"]))
        for m, v in (("Gross Profit Delta", b["dgp"]), ("Price Effect", b["price"]),
                     ("Cost Effect", b["cost"]), ("Mix Effect", b["mix"]),
                     ("Volume Effect", b["volume"]), ("New Products Effect", b["new"]),
                     ("Discontinued Effect", b["disc"]),
                     ("List Price Sub-effect", ss["list_effect"]),
                     ("Discount Sub-effect", ss["discount_effect"])):
            b3.append(row(test_id=f"B3-{gname}-{key}-{m.replace(' ','_')}", window_months=12,
                          filter_channel="(all)", filter_category="(all)", grain=gname,
                          entity=str(key), measure=m, expected=round(float(v), 10),
                          kind="money", tolerance=tolm))
pd.DataFrame(b3).to_csv(OUT / "baseline_B3_grains.csv", index=False)

# --------------------------------------------- B4 lifecycle, edges, union rule
b4 = []
life = prod.set_index("product_id").lifecycle
for pid, g in F.groupby("product_id"):
    lc = life[pid]
    if lc == "Continuing":
        continue
    b = bridge(lines_to_sku(g))
    step = "New Products Effect" if lc == "New in CP" else "Discontinued Effect"
    b4.append(row(test_id=f"B4-lifecycle-{pid}", window_months=12, filter_channel="(all)",
                  filter_category="(all)", grain="product", entity=pid,
                  measure=step, expected=round(float(b["dgp"]), 10), kind="money",
                  tolerance=1.0))
    for other in ("Price Effect", "Cost Effect", "Volume Effect", "Mix Effect"):
        b4.append(row(test_id=f"B4-lifecycle-{pid}-{other.replace(' ','_')}-zero", window_months=12,
                      filter_channel="(all)", filter_category="(all)", grain="product",
                      entity=pid, measure=other, expected=0.0, kind="money", tolerance=1.0))
pd.DataFrame(b4).to_csv(OUT / "baseline_B4_lifecycle.csv", index=False)

# ------------------------------------------------------------ B5 union table
def cs_agg(df):
    g = df.groupby(["customer_id", "product_id", "period"]).agg(
        q=("quantity", "sum"), r=("net_revenue", "sum"), k=("total_cost", "sum")).reset_index()
    g["p"] = g.r / g.q
    w_ = g.pivot_table(index=["customer_id", "product_id"], columns="period",
                       values=["q", "p", "r", "k"]).fillna(0)
    w_.columns = [f"{a}{'0' if b == 'PP' else '1'}" for a, b in w_.columns]
    for c in ["q0", "q1", "p0", "p1", "r0", "r1", "k0", "k1"]:
        if c not in w_:
            w_[c] = 0.0
    return w_.reset_index()


def union_set(df, n=12):
    # Phase 4 defect, closed: this originally took the n LOWEST dgp/price
    # values with no sign filter, so a selection with fewer than n genuinely
    # adverse customers on one side (or none, as here) padded the set with
    # customers tied at or above zero - not adverse at all. SP-03 defines the
    # rule as "top n BY ADVERSE dgp/price", which means filtering to <0 first,
    # exactly like the shipped [In Investigation List] DAX measure already
    # does. Found by a disagreement between DAX (12) and this baseline (24)
    # on a cell where every customer's price effect is exactly 0.0 - the DAX
    # measure was right; this function was wrong.
    w_ = cs_agg(df)
    comp = (w_.q0 > 0) & (w_.q1 > 0)
    qbar = np.where(comp, (w_.q0 + w_.q1) / 2, 0.0)
    t = pd.DataFrame({"customer_id": w_.customer_id,
                      "price": qbar * np.where(comp, w_.p1 - w_.p0, 0.0),
                      "dgp": (w_.r1 - w_.k1) - (w_.r0 - w_.k0),
                      "rev_cp": w_.r1}).groupby("customer_id").sum()
    a = t[t.dgp < 0].sort_values(["dgp", "rev_cp"], ascending=[True, False]).head(n).index
    b_ = t[t.price < 0].sort_values(["price", "rev_cp"], ascending=[True, False]).head(n).index
    return set(a) | set(b_), t


b5 = []
press = sorted(cust.loc[cust.discount_pressure, "customer_id"])
for w in (12, 6, 3):
    base = window(w)
    for ch in ["(all)"] + chans:
        for ca in ["(all)"] + cats:
            g = base
            if ch != "(all)":
                g = g[g.channel == ch]
            if ca != "(all)":
                g = g[g.category == ca]
            if g.empty:
                continue
            u, t = union_set(g)
            b5.append(row(test_id=f"B5-{w}m-{ch}-{ca}-rowcount".replace(" ", "_"), window_months=w,
                          filter_channel=ch, filter_category=ca, grain="account",
                          entity="(row set)", measure="Union Row Count",
                          expected=float(len(u)), kind="count", tolerance=0))
            if "C0056" in t.index:
                b5.append(row(test_id=f"B5-{w}m-{ch}-{ca}-C0056".replace(" ", "_"), window_months=w,
                              filter_channel=ch, filter_category=ca, grain="account",
                              entity="C0056", measure="In Investigation List",
                              expected=1.0 if "C0056" in u else 0.0, kind="count", tolerance=0))
            present = [p for p in press if p in t.index]
            b5.append(row(test_id=f"B5-{w}m-{ch}-{ca}-pressure".replace(" ", "_"), window_months=w,
                          filter_channel=ch, filter_category=ca, grain="account",
                          entity="(pressure group)", measure="Pressure Accounts Captured",
                          expected=float(len([p for p in present if p in u])), kind="count", tolerance=0))
pd.DataFrame(b5).to_csv(OUT / "baseline_B5_union_table.csv", index=False)

# ---------------------------------------------------------------- manifest
files = sorted(OUT.glob("baseline_*.csv"))
man = {
    "pack": "Gross Margin Bridge - Phase 4 acceptance baseline",
    "frozen": "2026-10-11",
    "dataset": "run7-accepted",
    "dataset_hashes": {f.name: hashlib.sha256((HERE / "data" / f.name).read_bytes()).hexdigest()
                       for f in sorted((HERE / "data").glob("*.csv"))},
    "methodology_module": {
        "file": "gmb_bridge.py",
        "sha256": hashlib.sha256((HERE / "gmb_bridge.py").read_bytes()).hexdigest()},
    "rounding_convention": {
        "computation": "IEEE 754 double, no intermediate rounding",
        "storage": "6+ decimals of USD; 10 decimals of ratio",
        "money_tolerance": "max(1.00, 1e-9 * |Gross Profit PP| of the selection)",
        "ratio_tolerance": "1e-9 absolute, on the ratio not the percent",
        "count_tolerance": "exact",
        "forbidden": "3-decimal $m display values as test inputs (+/- $500 error vs a $1.00 tolerance)"},
    "environment": {"python": sys.version.split()[0], "numpy": np.__version__, "pandas": pd.__version__},
    "files": {}}
total = 0
for f in files:
    df = pd.read_csv(f)
    total += len(df)
    man["files"][f.name] = {"rows": int(len(df)),
                            "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
man["total_expected_values"] = total
(OUT / "baseline_manifest.json").write_text(json.dumps(man, indent=2))

print(f"frozen baseline: {total:,} expected values across {len(files)} files")
for f in files:
    print(f"  {f.name:38s} {man['files'][f.name]['rows']:>6,} rows  {man['files'][f.name]['sha256'][:16]}")
print(f"  baseline_manifest.json")
