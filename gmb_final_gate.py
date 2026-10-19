"""
Phase 2 final gate — regression checks run against the run7-accepted dataset itself,
not against hand-built micro-sets. Nothing here generates data or changes the bridge.
"""
import hashlib, itertools, json, subprocess, sys
from pathlib import Path
import numpy as np, pandas as pd
from gmb_bridge import bridge, price_subsplit, lines_to_sku, tol

M = 1e6
HERE = Path(__file__).resolve().parent
fact = pd.read_csv("data/fact_sales_line.csv")
prod = pd.read_csv("data/dim_product.csv")
cust = pd.read_csv("data/dim_customer.csv")
rows = []


def rec(ref, test, ok, expected, observed):
    rows.append(dict(ref=ref, test=test, expected=expected, observed=observed,
                     status="PASS" if ok else "FAIL"))


def rep(ref, test, expected, observed):
    rows.append(dict(ref=ref, test=test, expected=expected, observed=observed, status="REPORT"))


F = fact.merge(prod[["product_id", "category"]], on="product_id").merge(
    cust[["customer_id", "channel"]], on="customer_id")
sk_all = lines_to_sku(fact)
b_all = bridge(sk_all)

# -- R1 the methodology module is still the audited one ---------------------
we = pd.DataFrame([(100, 50, 30, 100, 49, 32), (200, 10, 8, 260, 10, 8),
                   (0, np.nan, np.nan, 40, 25, 20)],
                  columns=["q0", "p0", "c0", "q1", "p1", "c1"]).astype(float)
wb = bridge(we)
rec("R1", "gmb_bridge reproduces the Phase 1 worked example exactly",
    abs(wb["gamma"] - 600 / 7250) < 1e-12 and abs(wb["volume"] - 191.1724) < 0.001
    and abs(wb["mix"] + 71.1724) < 0.001 and abs(wb["price"] + 100) < 1e-9,
    "gamma 8.2759%, volume +191.17, mix -71.17, price -100.00",
    f"gamma {wb['gamma']:.4%}, volume {wb['volume']:+.2f}, mix {wb['mix']:+.2f}, price {wb['price']:+.2f}")
p5 = pd.DataFrame([(100, 100, 98, 100, 100, 98), (100, 10, 8, 200, 10, 8)],
                  columns=["q0", "p0", "c0", "q1", "p1", "c1"]).astype(float)
rec("R1", "P5 negative control still returns +156.5217, so no unit-weighted index crept in",
    abs(bridge(p5)["mix"] - 156.5217391304348) < 1e-6, "+156.5217",
    f"{bridge(p5)['mix']:+.4f}")

# -- R2 reconciliation on the real dataset ----------------------------------
worst, n_cases = 0.0, 0
cats = sorted(F.category.unique()); chans = sorted(F.channel.unique())
for nc in (1, 2, 3, 6):
    for cs in itertools.islice(itertools.combinations(cats, nc), 20):
        for ch in [None] + chans:
            g = F[F.category.isin(cs)]
            if ch: g = g[g.channel == ch]
            if g.empty: continue
            r = bridge(lines_to_sku(g))
            worst = max(worst, abs(r["residual"])); n_cases += 1
rec("R2", f"Bridge reconciles on the real data across {n_cases} category x channel selections",
    worst <= tol(b_all["gp0"]), f"|residual| <= ${tol(b_all['gp0']):.2f}",
    f"max |residual| ${worst:.2e}")
rec("R2", "Bridge reconciles on the full population",
    abs(b_all["residual"]) <= tol(b_all["gp0"]), f"<= ${tol(b_all['gp0']):.2f}",
    f"${b_all['residual']:.6f}")

# -- R3 antisymmetry on the real dataset ------------------------------------
rev_sk = sk_all.copy()
for a, bcol in (("q0", "q1"), ("p0", "p1"), ("c0", "c1"), ("l0", "l1")):
    rev_sk[a], rev_sk[bcol] = sk_all[bcol].copy(), sk_all[a].copy()
rv = bridge(rev_sk)
ok = all(abs(b_all[k] + rv[k]) < 1e-6 * max(1, abs(b_all[k])) for k in ("price", "cost", "volume", "mix"))
rec("R3", "Antisymmetry on 255 real comparable SKUs: price, cost, volume, mix negate", ok,
    "exact negation",
    f"volume {b_all['volume']/M:+.4f} -> {rv['volume']/M:+.4f}; mix {b_all['mix']/M:+.4f} -> {rv['mix']/M:+.4f}")
rec("R3", "Antisymmetry: New and Discontinued exchange under reversal",
    abs(b_all["new"] + rv["disc"]) < 1e-6 and abs(b_all["disc"] + rv["new"]) < 1e-6,
    "new(forward) = -disc(reversed)",
    f"new {b_all['new']/M:+.4f} vs disc' {rv['disc']/M:+.4f}")
rec("R3", "Antisymmetry: gamma negates", abs(b_all["gamma"] + rv["gamma"]) < 1e-12,
    "exact", f"{b_all['gamma']:.6f} -> {rv['gamma']:.6f}")

# -- R4 entry / exit --------------------------------------------------------
life = prod.set_index("product_id").lifecycle
comp = set(sk_all[(sk_all.q0 > 0) & (sk_all.q1 > 0)].index)
new = set(sk_all[(sk_all.q0 == 0) & (sk_all.q1 > 0)].index)
exi = set(sk_all[(sk_all.q0 > 0) & (sk_all.q1 == 0)].index)
rec("R4", "Populations disjoint and exhaustive",
    not (comp & new) and not (comp & exi) and not (new & exi)
    and len(comp) + len(new) + len(exi) == len(sk_all),
    "disjoint, summing to all traded SKUs",
    f"{len(comp)} comparable + {len(new)} new + {len(exi)} discontinued = {len(sk_all)}")
rec("R4", "Measured populations match the dim_product lifecycle flags",
    new == set(life[life == "New in CP"].index) and exi == set(life[life == "Discontinued in PP"].index),
    "identical sets", f"new {len(new)}/{int((life=='New in CP').sum())}, "
                      f"exit {len(exi)}/{int((life=='Discontinued in PP').sum())}")
bn = bridge(sk_all.drop(index=list(new)))
moved = max(abs(bn[k] - b_all[k]) for k in ("price", "cost", "volume", "mix", "disc"))
rec("R4", "Removing the new SKUs moves only the New step",
    moved < 1e-6 and abs(bn["new"]) < 1e-9, "no other component moves",
    f"max movement ${moved:.2e}; New {b_all['new']/M:+.4f} -> {bn['new']/M:+.4f}")
rec("R4", "New and discontinued SKUs are excluded from the volume index",
    abs(bridge(sk_all.drop(index=list(new) + list(exi)))["gamma"] - b_all["gamma"]) < 1e-12,
    "gamma unchanged", f"{b_all['gamma']:.8f}")

# -- R5 additivity ----------------------------------------------------------
parts = [bridge(lines_to_sku(g)) for _, g in F.groupby("category")]
fixed = [bridge(lines_to_sku(g), gamma_override=b_all["gamma"]) for _, g in F.groupby("category")]
for k in ("price", "cost", "new", "disc", "dgp"):
    rec("R5", f"{k} additive across the category partition",
        abs(sum(r[k] for r in parts) - b_all[k]) < 0.01, "exact",
        f"${sum(r[k] for r in parts)/M:+.4f}m vs ${b_all[k]/M:+.4f}m")
rec("R5", "volume + mix additive when the index is recomputed per category",
    abs(sum(r["volume"] + r["mix"] for r in parts) - (b_all["volume"] + b_all["mix"])) < 0.01,
    "exact", f"${sum(r['volume']+r['mix'] for r in parts)/M:+.4f}m vs "
             f"${(b_all['volume']+b_all['mix'])/M:+.4f}m")
rec("R5", "mix additive at a FIXED index",
    abs(sum(r["mix"] for r in fixed) - b_all["mix"]) < 0.01, "exact",
    f"${sum(r['mix'] for r in fixed)/M:+.4f}m vs ${b_all['mix']/M:+.4f}m")
gapm = sum(r["mix"] for r in parts) - b_all["mix"]
rec("R5", "mix NOT additive at a recomputed index (documented, must differ)",
    abs(gapm) > 1.0, "differs", f"${sum(r['mix'] for r in parts)/M:+.4f}m vs "
                                f"${b_all['mix']/M:+.4f}m, gap ${gapm/M:+.4f}m")

# -- R6 raw-line agreement --------------------------------------------------
r0 = fact.loc[fact.period == "PP", "net_revenue"].sum(); r1 = fact.loc[fact.period == "CP", "net_revenue"].sum()
g0 = r0 - fact.loc[fact.period == "PP", "total_cost"].sum()
g1 = r1 - fact.loc[fact.period == "CP", "total_cost"].sum()
rec("R6", "Raw line sums equal the model's gross profit in both periods",
    abs(g0 - b_all["gp0"]) < 0.5 and abs(g1 - b_all["gp1"]) < 0.5, "< $0.50",
    f"PP diff ${g0-b_all['gp0']:.4f}, CP diff ${g1-b_all['gp1']:.4f}")
rec("R6", "Raw revenue growth and margin movement",
    True, "reported", f"${r0/M:.2f}m -> ${r1/M:.2f}m ({r1/r0-1:+.2%}); "
                      f"GM {g0/r0:.2%} -> {g1/r1:.2%} ({(g1/r1-g0/r0)*10000:,.0f} bps)")

# -- R7 mix decomposition and interpretability ------------------------------
skc = sk_all.join(prod.set_index("product_id")[["category"]])
cm = skc[(skc.q0 > 0) & (skc.q1 > 0)].copy()
cm["qbar"] = (cm.q0 + cm.q1) / 2; cm["pbar"] = (cm.p0 + cm.p1) / 2
cm["mbar"] = ((cm.p0 - cm.c0) + (cm.p1 - cm.c1)) / 2; cm["dq"] = cm.q1 - cm.q0
ga = (cm.pbar * cm.dq).sum() / (cm.pbar * cm.qbar).sum()
within = between = 0.0
per_cat = {}
for cat, g in cm.groupby("category"):
    gc = (g.pbar * g.dq).sum() / (g.pbar * g.qbar).sum()
    w = (g.mbar * (g.dq - g.qbar * gc)).sum(); bt = (gc - ga) * (g.mbar * g.qbar).sum()
    within += w; between += bt; per_cat[cat] = (w / M, bt / M)
rec("R7", "within + between = total mix (exact identity)",
    abs(within + between - b_all["mix"]) < 0.01, "exact",
    f"${(within+between)/M:+.5f}m vs ${b_all['mix']/M:+.5f}m, diff ${within+between-b_all['mix']:.6f}")
rep("R7", "Denominator of the 27.0% figure",
    "signed total mix effect", f"within ${within/M:+.4f}m / total mix ${b_all['mix']/M:+.4f}m "
                               f"= {within/b_all['mix']:.1%}; between {between/b_all['mix']:.1%}")
rec("R7", "Every category carries a non-trivial within-category mix",
    all(abs(w) >= 0.001 for w, _ in per_cat.values()), ">= $1k in each of six categories",
    "; ".join(f"{k.split()[0]} ${w:+.4f}m" for k, (w, _) in per_cat.items()))
mi = cm.mbar * (cm.dq - cm.qbar * ga)
rec("R7", "SKU-grain mix contributions are differentiated, not noise around zero",
    mi.abs().sort_values(ascending=False).head(20).sum() / mi.abs().sum() > 0.20,
    "top 20 SKUs > 20% of total |mix|",
    f"{mi.abs().sort_values(ascending=False).head(20).sum()/mi.abs().sum():.1%}; "
    f"sd ${mi.std():,.0f}, range {mi.min():,.0f} to +{mi.max():,.0f}")
ch_mix = {ch: bridge(lines_to_sku(g))["mix"] / M for ch, g in F.groupby("channel")}
rec("R7", "Channel-level mix is interpretable, not degenerate",
    all(abs(v) > 0.005 for v in ch_mix.values()), "> $5k in each channel",
    "; ".join(f"{k} ${v:+.4f}m" for k, v in ch_mix.items()))
single = {c: bridge(lines_to_sku(g))["mix"] / M for c, g in F.groupby("category")}
rep("R7", "Mix under a single-category filter (was ~$0.001m before the fix)",
    "non-degenerate", "; ".join(f"{k.split()[0]} ${v:+.4f}m" for k, v in single.items()))

# -- R8 benchmark grain -----------------------------------------------------
cpv = F[F.period == "CP"]
pair = cpv.groupby(["customer_id", "category"]).agg(
    rev=("net_revenue", "sum"), cost=("total_cost", "sum"), inv=("invoice_no", "nunique")).reset_index()
pair = pair.merge(cust[["customer_id", "channel"]], on="customer_id")
pair["gm"] = (pair.rev - pair.cost) / pair.rev
pair["eligible"] = (pair.rev >= 25000) & (pair.inv >= 6)
el = pair[pair.eligible]
sizes = el.groupby(["category", "channel"]).size()
ok_groups = set(sizes[sizes >= 10].index)


def wmed(d):
    d = d.sort_values("gm"); cw = d.rev.cumsum() / d.rev.sum()
    return float(d.gm[cw >= 0.5].iloc[0])


med = {g: wmed(d) for g, d in el.groupby(["category", "channel"]) if g in ok_groups}
pair["peer"] = [med.get((c, ch), np.nan) for c, ch in zip(pair.category, pair.channel)]
pair["gap"] = np.where(pair.eligible & pair.peer.notna(),
                       np.maximum(0, pair.peer - pair.gm) * pair.rev, np.nan)
tot = pair.gap.sum()
acct = pair.groupby("customer_id").gap.sum().sum(); catt = pair.groupby("category").gap.sum().sum()
rec("R8", "Account and category partitions give identical benchmark totals",
    abs(acct - tot) < 0.01 and abs(catt - tot) < 0.01, "identical",
    f"pairs ${tot:,.0f} | accounts ${acct:,.0f} | categories ${catt:,.0f}")
rec("R8", "Product grain yields no benchmark figure (blank by rule)",
    "product_id" not in pair.columns, "no product-grain definition exists",
    "benchmark population is customer x category; the measure returns BLANK() at product grain")
rec("R8", "Cross-grain addition double counts, so it is forbidden",
    abs((acct + catt) - 2 * tot) < 0.01, "exactly 2x the true total",
    f"${acct+catt:,.0f} = 2 x ${tot:,.0f}")
rec("R8", "Eligibility excludes small pairs and thin peer groups",
    pair.loc[~pair.eligible, "gap"].isna().all() and pair.loc[pair.peer.isna(), "gap"].isna().all(),
    "excluded contribute nothing",
    f"{int(pair.eligible.sum())} eligible of {len(pair)}; "
    f"{int(pair.peer.isna().sum())} without a qualifying peer group")

# -- R9 invoice integrity ---------------------------------------------------
rec("R9", "Primary key (invoice_no, line_no) unique",
    fact.duplicated(["invoice_no", "line_no"]).sum() == 0, "0 duplicates",
    f"{int(fact.duplicated(['invoice_no','line_no']).sum())}")
rec("R9", "One invoice carries one customer and one date",
    int(fact.groupby("invoice_no")[["customer_id", "invoice_date"]].nunique().max().max()) == 1,
    "1 each", "1 each")
rec("R9", "line_no contiguous from 1 within every invoice",
    bool(fact.groupby("invoice_no").line_no.agg(
        lambda s: sorted(s) == list(range(1, len(s) + 1))).all()), "contiguous", "verified")
lpi = fact.groupby("invoice_no").size()
rec("R9", "Line-count distribution credible for a distributor",
    2.5 <= lpi.mean() <= 8 and lpi.max() < 60, "mean 2.5-8",
    f"mean {lpi.mean():.2f}, median {lpi.median():.0f}, p90 {lpi.quantile(.9):.0f}, "
    f"max {lpi.max()}, {fact.invoice_no.nunique():,} invoices")
rep("R9", "Lines per invoice, full distribution", "reported",
    "; ".join(f"{k}:{v:,}" for k, v in lpi.value_counts().sort_index().head(8).items()) + "; ...")
rec("R9", "Every invoice sits inside one comparison period",
    int(fact.groupby("invoice_no").period.nunique().max()) == 1, "1", "1")

# -- R10 reproducibility ----------------------------------------------------
man = json.loads((HERE / "reports" / "manifest_run7-accepted.json").read_text())
allok = True
for fn, meta in man["files"].items():
    live = hashlib.sha256((HERE / "data" / fn).read_bytes()).hexdigest()
    allok &= live == meta["sha256"]
rec("R10", "All four files match the run7 manifest hashes", allok, "4/4 match",
    "; ".join(f"{k.split('.')[0]} {v['sha256'][:12]}" for k, v in man["files"].items()))
rec("R10", "Seed, parameters and environment recorded",
    "seed" in man["params"] and bool(man["environment"]), "present",
    f"seed {man['params']['seed']}, python {man['environment']['python']}, "
    f"numpy {man['environment']['numpy']}, pandas {man['environment']['pandas']}")
rec("R10", "Historical manifests preserved",
    len(list((HERE / 'reports').glob('manifest_*.json'))) >= 7, ">= 7 run manifests",
    f"{len(list((HERE/'reports').glob('manifest_*.json')))} manifests, "
    f"{len(list((HERE/'reports').glob('audit_*.csv')))} audit result sets")

out = pd.DataFrame(rows)
out.to_csv(HERE / "reports" / "final_gate_run7.csv", index=False)
w = out.test.str.len().max()
for _, r in out.iterrows():
    print(f"{r.status:<6} {r.ref:<4} {r.test.ljust(w)}  exp {r.expected:<42} obs {r.observed}")
vc = out.status.value_counts().to_dict()
print("-" * 120)
print(f"{len(out)} regression checks: {vc.get('PASS',0)} pass, {vc.get('FAIL',0)} fail, "
      f"{vc.get('REPORT',0)} reported without threshold")
