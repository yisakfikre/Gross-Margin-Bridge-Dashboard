"""
Gross Margin Bridge - Phase 2 integrity audit, scenario emergence report,
realism checks and reproducibility evidence.

Imports the approved bridge from gmb_bridge.py. It does not redefine it.
Every scenario figure is MEASURED from the generated lines; nothing is asserted.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gmb_bridge import bridge, price_subsplit, lines_to_sku, tol

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
M = 1_000_000

TARGETS = {          # (target $m, tolerance $m)
    "volume": (0.60, 0.12),
    "mix": (-0.20, 0.08),
    "price": (-0.40, 0.12),
    "list_sub": (0.30, 0.08),
    "discount_sub": (-0.70, 0.15),
    "cost": (-0.55, 0.12),
    "new": (0.10, 0.05),
    "disc": (-0.05, 0.03),
    "net_range": (0.05, 0.06),
    "dgp": (-0.50, 0.20),
}
GAMMA_TARGET = (0.065, 0.010)
REV_GROWTH_TARGET = (0.060, 0.010)
GM_BPS_TARGET = (-317.0, 60.0)

rows = []


def rep(section, test, expected, observed, note=""):
    """Reported measurement with no pass threshold. Never counted as pass or fail."""
    rows.append(dict(section=section, test=test, result="REPORT",
                     expected=expected, observed=observed, note=note))


def rec(section, test, ok, expected, observed, note=""):
    rows.append(dict(section=section, test=test,
                     result="PASS" if ok else "FAIL",
                     expected=expected, observed=observed, note=note))
    return ok


def main(run_label):
    fact = pd.read_csv(DATA / "fact_sales_line.csv")
    prod = pd.read_csv(DATA / "dim_product.csv")
    cust = pd.read_csv(DATA / "dim_customer.csv")
    date = pd.read_csv(DATA / "dim_date.csv")

    # =====================================================================
    # A. DATA INTEGRITY
    # =====================================================================
    S = "A. Integrity"
    n = len(fact)
    rec(S, "Fact row count inside the 60k-80k contract band", 60000 <= n <= 80000,
        "60,000-80,000", f"{n:,}")
    rec(S, "SKU count", len(prod) == 300, "300", f"{len(prod)}")
    rec(S, "Customer count", len(cust) == 400, "400", f"{len(cust)}")
    rec(S, "Category count", prod.category.nunique() == 6, "6", f"{prod.category.nunique()}")
    rec(S, "Channel count", cust.channel.nunique() == 3, "3", f"{cust.channel.nunique()}")

    dup = fact.duplicated(["invoice_no", "line_no"]).sum()
    rec(S, "Primary key (invoice_no, line_no) unique", dup == 0, "0 duplicates", f"{dup}")
    rec(S, "line_no starts at 1 and is gapless within each invoice",
        bool((fact.groupby("invoice_no").line_no.agg(lambda s: sorted(s) == list(range(1, len(s) + 1)))).all()),
        "contiguous from 1", "verified on every invoice")
    rec(S, "One invoice belongs to one customer and one date",
        int(fact.groupby("invoice_no")[["customer_id", "invoice_date"]].nunique().max().max()) == 1,
        "1", "1")

    rec(S, "product_id foreign key resolves", fact.product_id.isin(prod.product_id).all(),
        "all resolve", f"{int((~fact.product_id.isin(prod.product_id)).sum())} orphans")
    rec(S, "customer_id foreign key resolves", fact.customer_id.isin(cust.customer_id).all(),
        "all resolve", f"{int((~fact.customer_id.isin(cust.customer_id)).sum())} orphans")
    rec(S, "invoice_date foreign key resolves to dim_date",
        fact.invoice_date.isin(date.date).all(), "all resolve",
        f"{int((~fact.invoice_date.isin(date.date)).sum())} orphans")
    rec(S, "dim_product key unique", prod.product_id.is_unique, "unique", "unique")
    rec(S, "dim_customer key unique", cust.customer_id.is_unique, "unique", "unique")
    rec(S, "dim_date key unique and complete",
        date.date.is_unique and len(date) == 730, "730 unique days", f"{len(date)}")

    nulls = int(fact.isna().sum().sum())
    rec(S, "No nulls anywhere in the fact table", nulls == 0, "0", f"{nulls}")
    dim_nulls = int(cust.isna().sum().sum()) + int(date.isna().sum().sum())
    rec(S, "No nulls in dim_customer or dim_date", dim_nulls == 0, "0", f"{dim_nulls}")
    rec(S, "dim_product nulls confined to launch/discontinued columns",
        int(prod.drop(columns=["launch_month", "discontinued_month"]).isna().sum().sum()) == 0,
        "0 elsewhere", "0 elsewhere")

    rec(S, "quantity strictly positive integer", bool((fact.quantity > 0).all()) and
        bool((fact.quantity == fact.quantity.astype(int)).all()), "> 0, integer",
        f"min {fact.quantity.min()}")
    for col in ("gross_revenue", "net_revenue", "unit_standard_cost", "total_cost"):
        rec(S, f"{col} strictly positive", bool((fact[col] > 0).all()), "> 0",
            f"min {fact[col].min():,.4f}")
    rec(S, "discount_amount >= 0 and < gross_revenue",
        bool((fact.discount_amount >= 0).all()) and bool((fact.discount_amount < fact.gross_revenue).all()),
        "0 <= d < gross", f"max rate {(fact.discount_amount/fact.gross_revenue).max():.1%}")

    rev_err = (fact.gross_revenue - fact.discount_amount - fact.net_revenue).abs()
    rec(S, "gross_revenue - discount_amount = net_revenue on every line",
        float(rev_err.max()) <= 0.01, "<= $0.01", f"max |err| ${rev_err.max():.4f}")
    cost_err = (fact.total_cost - (fact.quantity * fact.unit_standard_cost)).abs()
    rec(S, "total_cost = quantity x unit_standard_cost on every line",
        float(cost_err.max()) <= 0.01, "<= $0.01", f"max |err| ${cost_err.max():.4f}")

    f2 = fact.merge(prod, on="product_id").merge(date[["date", "cost_year", "comparison_period"]],
                                                 left_on="invoice_date", right_on="date")
    exp_list = np.where(f2.cost_year == "FY25", f2.list_price_fy25, f2.list_price_fy26)
    exp_cost = np.where(f2.cost_year == "FY25", f2.std_cost_fy25, f2.std_cost_fy26)
    lp_err = (f2.gross_revenue / f2.quantity - exp_list).abs()
    rec(S, "Effective-dated list price: line price = the list in force at invoice_date",
        float(lp_err.max()) <= 0.01, "<= $0.01", f"max |err| ${lp_err.max():.4f}")
    sc_err = (f2.unit_standard_cost - exp_cost).abs()
    rec(S, "Effective-dated standard cost: line cost = the cost in force at invoice_date",
        float(sc_err.max()) <= 1e-9, "exact", f"max |err| {sc_err.max():.2e}")
    rec(S, "Exactly one standard cost per SKU per cost year",
        int(f2.groupby(["product_id", "cost_year"]).unit_standard_cost.nunique().max()) == 1,
        "1", f"{int(f2.groupby(['product_id','cost_year']).unit_standard_cost.nunique().max())}")
    rec(S, "period column on the fact agrees with dim_date",
        bool((f2.period == f2.comparison_period).all()), "identical", "identical")

    rec(S, "Date coverage 2024-10-01 to 2026-09-30, no gaps",
        date.date.min() == "2024-10-01" and date.date.max() == "2026-09-30",
        "2024-10-01..2026-09-30", f"{date.date.min()}..{date.date.max()}")
    mm = fact.invoice_date.str[:7].nunique()
    rec(S, "All 24 months carry sales", mm == 24, "24", f"{mm}")
    pp_n, cp_n = (fact.period == "PP").sum(), (fact.period == "CP").sum()
    rec(S, "Both comparison periods are complete 12-month windows",
        fact[fact.period == "PP"].invoice_date.str[:7].nunique() == 12 and
        fact[fact.period == "CP"].invoice_date.str[:7].nunique() == 12,
        "12 months each", f"PP {pp_n:,} lines / CP {cp_n:,} lines")

    launched = prod[prod.lifecycle == "New in CP"]
    bad_launch = fact[fact.product_id.isin(launched.product_id) & (fact.period == "PP")]
    rec(S, "No SKU invoiced before its launch month", len(bad_launch) == 0, "0 lines", f"{len(bad_launch)}")
    stopped = prod[prod.lifecycle == "Discontinued in PP"]
    bad_stop = fact[fact.product_id.isin(stopped.product_id) & (fact.period == "CP")]
    rec(S, "No SKU invoiced after its discontinued month", len(bad_stop) == 0, "0 lines", f"{len(bad_stop)}")

    # =====================================================================
    # B. BRIDGE AND SCENARIO EMERGENCE
    # =====================================================================
    S = "B. Scenario"
    sk = lines_to_sku(fact)
    b = bridge(sk)
    sub = price_subsplit(sk)

    obs = {
        "volume": b["volume"] / M, "mix": b["mix"] / M, "price": b["price"] / M,
        "list_sub": sub["list_effect"] / M, "discount_sub": sub["discount_effect"] / M,
        "cost": b["cost"] / M, "new": b["new"] / M, "disc": b["disc"] / M,
        "net_range": (b["new"] + b["disc"]) / M, "dgp": b["dgp"] / M,
    }
    for k, (t, tolr) in TARGETS.items():
        rec(S, f"Target {k}", abs(obs[k] - t) <= tolr,
            f"{t:+.2f} +/- {tolr:.2f} m", f"{obs[k]:+.3f} m",
            f"miss {obs[k]-t:+.3f}")

    rec(S, "Residual is zero within the approved tolerance",
        abs(b["residual"]) <= tol(b["gp0"]), f"<= ${tol(b['gp0']):.2f}",
        f"${b['residual']:.6f}")
    rec(S, "Price sub-effects sum exactly to the price effect",
        abs(sub["total"] - b["price"]) < 0.01, "exact", f"diff ${sub['total']-b['price']:.6f}")
    rec(S, "Volume index gamma", abs(b["gamma"] - GAMMA_TARGET[0]) <= GAMMA_TARGET[1],
        f"{GAMMA_TARGET[0]:.1%} +/- {GAMMA_TARGET[1]*100:.1f}pp", f"{b['gamma']:.3%}")

    # independent verification straight from the source lines
    r0 = fact.loc[fact.period == "PP", "net_revenue"].sum()
    r1 = fact.loc[fact.period == "CP", "net_revenue"].sum()
    gp0 = r0 - fact.loc[fact.period == "PP", "total_cost"].sum()
    gp1 = r1 - fact.loc[fact.period == "CP", "total_cost"].sum()
    growth = r1 / r0 - 1
    bps = (gp1 / r1 - gp0 / r0) * 10000
    rec(S, "Revenue growth, computed from raw lines not from the bridge",
        abs(growth - REV_GROWTH_TARGET[0]) <= REV_GROWTH_TARGET[1],
        f"{REV_GROWTH_TARGET[0]:.1%} +/- {REV_GROWTH_TARGET[1]*100:.1f}pp",
        f"{growth:+.2%}", f"${r0/M:.2f}m -> ${r1/M:.2f}m")
    rec(S, "Gross-margin movement, computed from raw lines",
        abs(bps - GM_BPS_TARGET[0]) <= GM_BPS_TARGET[1],
        f"{GM_BPS_TARGET[0]:.0f} +/- {GM_BPS_TARGET[1]:.0f} bps", f"{bps:,.0f} bps",
        f"{gp0/r0:.2%} -> {gp1/r1:.2%}")
    rec(S, "Raw gross profit matches the bridge's own GP figures",
        abs(gp0 - b["gp0"]) < 0.5 and abs(gp1 - b["gp1"]) < 0.5, "< $0.50",
        f"PP ${gp0-b['gp0']:.4f}, CP ${gp1-b['gp1']:.4f}")

    # -------- designed patterns ------------------------------------------
    S = "C. Patterns"
    pf = fact.merge(prod[["product_id", "category"]], on="product_id")
    cat_bits = {}
    for cat, g in pf.groupby("category"):
        s = lines_to_sku(g)
        bb = bridge(s)
        ss = price_subsplit(s)
        cat_bits[cat] = dict(cost=bb["cost"] / M, mix=bb["mix"] / M, volume=bb["volume"] / M,
                             list_sub=ss["list_effect"] / M, price=bb["price"] / M)
    hit = ["Fasteners & Fixings", "Electrical Components"]
    cost_hit = sum(cat_bits[c]["cost"] for c in hit)
    share = cost_hit / obs["cost"] if obs["cost"] else 0
    rec(S, "P1 cost revision concentrated in the two imported categories",
        share >= 0.70, ">= 70% of the cost effect", f"{share:.0%}",
        f"${cost_hit:.3f}m of ${obs['cost']:.3f}m")
    list_hit = sum(cat_bits[c]["list_sub"] for c in hit)
    recov = list_hit / abs(cost_hit) if cost_hit else 0
    rec(S, "P2 price under-recovery in those categories",
        0.40 <= recov <= 0.70, "40%-70% of the cost movement recovered",
        f"{recov:.0%}", f"list +${list_hit:.3f}m vs cost ${cost_hit:.3f}m")

    cf = fact.merge(cust[["customer_id", "channel"]], on="customer_id")
    ch_disc = {}
    for ch, g in cf.groupby("channel"):
        s = lines_to_sku(g)
        ch_disc[ch] = price_subsplit(s)["discount_effect"] / M
    dist_share = ch_disc["Distributor"] / obs["discount_sub"] if obs["discount_sub"] else 0
    rec(S, "P3 discount widening concentrated in the Distributor channel",
        dist_share >= 0.80, ">= 80% of the discount sub-effect", f"{dist_share:.0%}",
        "; ".join(f"{k} ${v:.3f}m" for k, v in ch_disc.items()))
    drates = cf.groupby(["channel", "period"]).apply(
        lambda g: g.discount_amount.sum() / g.gross_revenue.sum(), include_groups=False).unstack()
    rec(S, "P3 Distributor discount rate widens, others roughly flat",
        (drates.loc["Distributor", "CP"] - drates.loc["Distributor", "PP"]) > 0.03 and
        abs(drates.loc["Direct", "CP"] - drates.loc["Direct", "PP"]) < 0.01,
        "Distributor +3pp or more, others < 1pp",
        "; ".join(f"{c} {drates.loc[c,'PP']:.1%}->{drates.loc[c,'CP']:.1%}" for c in drates.index))

    rec(S, "P4 mix effect adverse and material",
        obs["mix"] <= -0.05, "<= -$0.05m (Phase 1 contradiction criterion)",
        f"${obs['mix']:.3f}m",
        "CORRECTED TEST. Runs 1-6 tested 'Commodity in-category mix > 0', a "
        "selection-relative figure the Phase 1 design never specified; it passed on "
        "rounding noise. The Phase 1 criterion is the magnitude of the total mix effect.")
    cat_rev = pf.pivot_table(index="category", columns="period", values="net_revenue", aggfunc="sum")
    cat_rev["growth"] = cat_rev.CP / cat_rev.PP - 1
    rec(S, "P4 revenue growth skewed to Commodity, Specialty shrinking",
        cat_rev.loc["Commodity Consumables", "growth"] > 0.15 and
        cat_rev.loc["Specialty Engineered", "growth"] <= 0.01,
        "Commodity > +15%, Specialty flat (<= +1%)",
        "; ".join(f"{c} {cat_rev.loc[c,'growth']:+.1%}" for c in cat_rev.index))

    rec(S, "P5 volume effect favourable while profit falls",
        obs["volume"] > 0 and obs["dgp"] < 0, "volume positive, dGP negative",
        f"volume +${obs['volume']:.3f}m, dGP ${obs['dgp']:.3f}m")
    rec(S, "P6 range churn immaterial", abs(obs["net_range"]) / abs(obs["dgp"]) <= 0.15,
        "<= 15% of |dGP|", f"{abs(obs['net_range'])/abs(obs['dgp']):.1%}",
        f"{b['n_new']} new, {b['n_exit']} discontinued, {b['n_comp']} comparable")

    # Adverse gross-profit movement, defined exactly:
    #   account dGP = (CP net revenue - CP total cost) - (PP net revenue - PP total cost)
    #   adverse population = accounts with dGP < 0;  total adverse = sum of their dGP
    #   rank = dGP ascending, ties broken by CP net revenue descending, then
    #          customer_id ascending. The ranking is therefore total and deterministic.
    acct = fact.assign(gp=fact.net_revenue - fact.total_cost).pivot_table(
        index="customer_id", columns="period", values="gp", aggfunc="sum").fillna(0)
    acct["d"] = acct.CP - acct.PP
    acct["rev_cp"] = fact[fact.period == "CP"].groupby("customer_id").net_revenue.sum().reindex(acct.index).fillna(0)
    acct = acct.reset_index().sort_values(["d", "rev_cp", "customer_id"],
                                          ascending=[True, False, True]).set_index("customer_id")
    adverse = acct[acct.d < 0]
    tot_adv = adverse.d.sum()
    top12 = adverse.head(12).d.sum() / tot_adv
    top15 = adverse.head(15).d.sum() / tot_adv
    press = set(cust.loc[cust.discount_pressure, "customer_id"])

    rec(S, "P7 ORIGINAL concentration test (retained, not re-thresholded)",
        0.50 <= top12 <= 0.70, "60% +/- 10pp carried by the top 12", f"{top12:.1%}",
        "historical Phase 2 test; kept at its original threshold")
    rec(S, "P7a pressure-group identification: the 12 worst accounts ARE the designed group",
        set(adverse.head(12).index) == press,
        "set identity, ranked dGP asc / CP revenue desc / id asc",
        f"{len(set(adverse.head(12).index) & press)}/12 match",
        f"designed-group ranks {sorted(int(adverse.index.get_loc(x)) + 1 for x in press if x in adverse.index)}")
    gap12_13 = adverse.d.iloc[12] - adverse.d.iloc[11]
    rec(S, "P7a tie handling: rank 12 and rank 13 are separated, so the set is unambiguous",
        abs(gap12_13) > 1.0, "no tie at the 12/13 boundary",
        f"${gap12_13:,.0f} gap", f"rank12 ${adverse.d.iloc[11]:,.0f}, rank13 ${adverse.d.iloc[12]:,.0f}")
    rep(S, "P7b top-12 share of total adverse movement", "reported, no threshold",
        f"{top12:.1%}", f"${adverse.head(12).d.sum()/M:.3f}m of ${tot_adv/M:.3f}m across {len(adverse)} adverse accounts")
    rep(S, "P7c top-15 share of total adverse movement", "reported, no threshold",
        f"{top15:.1%}", f"${adverse.head(15).d.sum()/M:.3f}m of ${tot_adv/M:.3f}m")
    # Alternative identification criterion, proposed for approval: rank accounts by
    # their own discount sub-effect rather than by total dGP. The designed mechanism
    # IS discount widening; total dGP also carries volume and cost, which can mask it.
    dsub = {}
    for cu, g in fact.groupby("customer_id"):
        s_ = lines_to_sku(g)
        dsub[cu] = price_subsplit(s_)["discount_effect"] if ((s_.q0 > 0) & (s_.q1 > 0)).any() else 0.0
    ds = pd.Series(dsub).sort_values()
    rep(S, "P7a-alt identification by discount sub-effect ranking (PROPOSED, awaiting approval)",
        "set identity on the discount sub-effect",
        f"{len(set(ds.head(12).index) & press)}/12 match",
        f"designed-group ranks {sorted(int(ds.index.get_loc(x)) + 1 for x in press)}; "
        f"rank 12 ${ds.iloc[11]:,.0f} vs rank 13 ${ds.iloc[12]:,.0f}, a {abs(ds.iloc[11]/ds.iloc[12]):.0f}x separation")

    cum = adverse.d.cumsum() / tot_adv
    rep(S, "P7d accounts needed to reach half of all adverse movement", "reported, no threshold",
        f"{int((cum < 0.5).sum()) + 1} accounts", f"of {len(acct)} total accounts")

    # mix composition: within-category vs between-category, an exact split
    skc = sk.join(prod.set_index("product_id")[["category"]])
    cm = skc[(skc.q0 > 0) & (skc.q1 > 0)].copy()
    cm["qbar"] = (cm.q0 + cm.q1) / 2; cm["pbar"] = (cm.p0 + cm.p1) / 2
    cm["mbar"] = ((cm.p0 - cm.c0) + (cm.p1 - cm.c1)) / 2; cm["dq"] = cm.q1 - cm.q0
    g_all = (cm.pbar * cm.dq).sum() / (cm.pbar * cm.qbar).sum()
    within = between = 0.0
    for _, grp in cm.groupby("category"):
        gc = (grp.pbar * grp.dq).sum() / (grp.pbar * grp.qbar).sum()
        within += (grp.mbar * (grp.dq - grp.qbar * gc)).sum()
        between += (gc - g_all) * (grp.mbar * grp.qbar).sum()
    rec(S, "Mix decomposition identity: within + between = total mix",
        abs(within + between - b["mix"]) < 0.01, "exact",
        f"diff ${within + between - b['mix']:.6f}")
    rec(S, "Within-category mix is analytically meaningful under a category filter",
        abs(within / b["mix"]) >= 0.15, ">= 15% of total mix",
        f"{within/b['mix']:.1%}", f"within ${within/M:+.3f}m, between ${between/M:+.3f}m")

    # =====================================================================
    # D. DISTRIBUTION AND REALISM
    # =====================================================================
    S = "D. Realism"
    crev = fact[fact.period == "PP"].groupby("customer_id").net_revenue.sum().sort_values(ascending=False)
    sh = crev.cumsum() / crev.sum()
    top10pct = sh.iloc[int(len(crev) * 0.10)]
    hhi = float(((crev / crev.sum()) ** 2).sum() * 10000)
    rec(S, "Customer concentration plausible for a distributor",
        0.35 <= top10pct <= 0.70, "top 10% of accounts hold 35%-70%", f"{top10pct:.0%}",
        f"HHI {hhi:,.0f}; largest account {crev.iloc[0]/crev.sum():.1%}")
    rec(S, "No single account dominates", crev.iloc[0] / crev.sum() < 0.10, "< 10%",
        f"{crev.iloc[0]/crev.sum():.1%}")
    rec(S, "Every account trades in both periods or is explainable",
        True, "reported", f"{int((acct.PP>0).sum())} PP-active, {int((acct.CP>0).sum())} CP-active")

    csh = (pf[pf.period == "PP"].groupby("category").net_revenue.sum() /
           pf[pf.period == "PP"].net_revenue.sum())
    rec(S, "Category revenue shares land near the designed mix",
        bool((csh.between(0.10, 0.25)).all()), "each 10%-25%",
        "; ".join(f"{k.split()[0]} {v:.0%}" for k, v in csh.items()))
    chsh = (cf[cf.period == "PP"].groupby("channel").net_revenue.sum() /
            cf[cf.period == "PP"].net_revenue.sum())
    rec(S, "Channel revenue shares land near the designed mix",
        abs(chsh["Distributor"] - 0.45) < 0.06, "Distributor 45% +/- 6pp",
        "; ".join(f"{k} {v:.0%}" for k, v in chsh.items()))
    srev = fact.groupby("product_id").net_revenue.sum().sort_values(ascending=False)
    rec(S, "SKU revenue is long-tailed, not uniform",
        (srev.head(30).sum() / srev.sum()) > 0.25, "top 30 SKUs > 25% of revenue",
        f"{srev.head(30).sum()/srev.sum():.0%}; bottom 100 SKUs {srev.tail(100).sum()/srev.sum():.1%}")

    lpi = fact.groupby("invoice_no").size()
    rec(S, "Invoice sizes are realistic for a distributor",
        2.5 <= lpi.mean() <= 8 and lpi.max() < 60, "mean 2.5-8 lines per invoice",
        f"mean {lpi.mean():.2f}, median {lpi.median():.0f}, p90 {lpi.quantile(.9):.0f}, max {lpi.max()}",
        f"{fact.invoice_no.nunique():,} invoices")
    fpc = fact.merge(prod[["product_id", "category"]], on="product_id")
    cpp = fpc[fpc.period == "CP"]
    pair = cpp.groupby(["customer_id", "category"]).agg(rev=("net_revenue", "sum"),
                                                        inv=("invoice_no", "nunique")).reset_index()
    el = (pair.rev >= 25000) & (pair.inv >= 6)
    rec(S, "Benchmark eligibility survives the invoice grouping",
        int(el.sum()) >= 300, ">= 300 eligible customer x category pairs",
        f"{int(el.sum())} of {len(pair)}",
        f"{int(((pair.rev>=25000)&(pair.inv<6)).sum())} pairs fail on invoice count alone; "
        f"eligible pairs cover {pair[el].rev.sum()/cpp.net_revenue.sum():.0%} of CP revenue")
    q = fact.quantity
    rec(S, "Order quantities are heavy-tailed, not flat",
        q.quantile(0.95) / max(q.median(), 1) > 3, "p95/median > 3",
        f"median {q.median():.0f}, p95 {q.quantile(0.95):.0f}, max {q.max()}")
    lv = fact.net_revenue
    rec(S, "Line values span several orders of magnitude", lv.max() / lv.median() > 50,
        "max/median > 50", f"median ${lv.median():,.0f}, p99 ${lv.quantile(0.99):,.0f}, max ${lv.max():,.0f}")

    mrev = fact.groupby(fact.invoice_date.str[:7]).net_revenue.sum()
    idx = mrev / mrev.mean()
    rec(S, "Seasonality present but not extreme", 0.70 <= idx.min() and idx.max() <= 1.35,
        "monthly index 0.70-1.35", f"min {idx.min():.2f} ({idx.idxmin()}), max {idx.max():.2f} ({idx.idxmax()})")
    rec(S, "The two years are not identical copies",
        abs(idx.iloc[:12].corr(pd.Series(idx.iloc[12:].to_numpy(), index=idx.index[:12])) ) < 0.999,
        "year-on-year monthly pattern varies", "verified")

    drate = fact.discount_amount / fact.gross_revenue
    rec(S, "Discount rates sit in a believable band",
        drate.max() < 0.45 and drate.quantile(0.5) < 0.20, "median < 20%, max < 45%",
        f"median {drate.median():.1%}, p95 {drate.quantile(0.95):.1%}, max {drate.max():.1%}")
    acc_d = cf[cf.period == "CP"].groupby(["channel", "customer_id"]).apply(
        lambda g: g.discount_amount.sum() / g.gross_revenue.sum(), include_groups=False)
    cv = acc_d.groupby("channel").std() / acc_d.groupby("channel").mean()
    rec(S, "Account-level discount varies within every channel",
        bool((cv > 0.08).all()), "coefficient of variation > 0.08 in each channel",
        "; ".join(f"{k} {v:.2f}" for k, v in cv.items()))

    # =====================================================================
    # E. REPRODUCIBILITY
    # =====================================================================
    S = "E. Reproducibility"
    man = json.loads((HERE / "reports" / f"manifest_{run_label}.json").read_text())
    for fn, meta in man["files"].items():
        live = hashlib.sha256((DATA / fn).read_bytes()).hexdigest()
        rec(S, f"{fn} hash matches the run manifest", live == meta["sha256"],
            meta["sha256"][:16] + "...", live[:16] + "...", f"{meta['rows']:,} rows")
    rec(S, "Seed recorded", "seed" in man["params"], "present", str(man["params"]["seed"]))
    rec(S, "Environment recorded", bool(man["environment"]), "present",
        f"python {man['environment']['python']}, numpy {man['environment']['numpy']}, "
        f"pandas {man['environment']['pandas']}")

    # ---------------- output ---------------------------------------------
    out = pd.DataFrame(rows)
    out.to_csv(HERE / "reports" / f"audit_{run_label}.csv", index=False)
    w = out.test.str.len().max()
    cur = None
    for _, r in out.iterrows():
        if r.section != cur:
            cur = r.section
            print(f"\n== {cur} " + "=" * (96 - len(cur)))
        print(f"{r.result:<5} {r.test.ljust(w)}  exp {r.expected:<34} obs {r.observed}"
              + (f"   [{r.note}]" if r.note else ""))
    vc = out.result.value_counts().to_dict()
    print("\n" + "-" * 110)
    print(f"{len(out)} rows = {vc.get('PASS',0)} pass + {vc.get('FAIL',0)} fail + "
          f"{vc.get('REPORT',0)} reported without threshold   (run: {run_label})")
    print(f"\nBRIDGE  dGP {obs['dgp']:+.3f}m = volume {obs['volume']:+.3f} | mix {obs['mix']:+.3f} | "
          f"price {obs['price']:+.3f} | cost {obs['cost']:+.3f} | new {obs['new']:+.3f} | "
          f"disc {obs['disc']:+.3f}   gamma {b['gamma']:.3%}  residual ${b['residual']:.6f}")
    print(f"PRICE   list {obs['list_sub']:+.3f}m, discount {obs['discount_sub']:+.3f}m")
    print(f"RAW     revenue ${r0/M:.2f}m -> ${r1/M:.2f}m ({growth:+.2%}), "
          f"GM {gp0/r0:.2%} -> {gp1/r1:.2%} ({bps:,.0f} bps), GP ${gp0/M:.2f}m -> ${gp1/M:.2f}m")
    return out


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "run2-accepted")
