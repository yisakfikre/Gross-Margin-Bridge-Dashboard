"""
Gross Margin Bridge - Phase 2 synthetic dataset generator.

Seeded and reproducible. Produces the four contracted source files:
    data/fact_sales_line.csv
    data/dim_product.csv
    data/dim_customer.csv
    data/dim_date.csv

The disconnected period selector is NOT a source file. It is a Power BI model
artifact and is documented in the data dictionary only.

Nothing in this generator writes a bridge component value. The designed scenario
is implemented through transaction-level assumptions (price lists, standard-cost
revisions, discount rates, category growth) and the resulting bridge is measured
afterwards by gmb_audit.py.

Usage:  python3 gmb_generate.py [--seed N] [--run-label NAME] [--params FILE.json]
"""
import argparse
import json
import hashlib
import calendar
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
REPORTS = HERE / "reports"

# ---------------------------------------------------------------------------
# PARAMETERS - every designed pattern is expressed here, nowhere else
# ---------------------------------------------------------------------------
PARAMS = {
    "seed": 20261010,
    "run_label": "run7-accepted",
    "pp_start": "2024-10-01", "pp_end": "2025-09-30",
    "cp_start": "2025-10-01", "cp_end": "2026-09-30",
    "cost_revision_effective": "1 October each year",
    "target_pp_net_revenue_musd": 32.00,
    "n_customers": 400,
    "n_skus": 300,
    "n_new_skus": 25,
    "n_discontinued_skus": 20,

    # category: revenue share (PP), gross-margin rate, FY26 standard-cost uplift,
    #           FY26 list-price uplift, CP unit-volume growth, avg list price,
    #           typical units per invoice line
    "categories": {
        "Commodity Consumables": dict(share=0.20, gm=0.19, cost_up=0.005, list_up=0.000,
                                      vol_growth=0.240, avg_price=18.0, line_qty=80),
        "Fasteners & Fixings":   dict(share=0.15, gm=0.28, cost_up=0.075, list_up=0.030,
                                      vol_growth=0.050, avg_price=25.0, line_qty=64),
        "Electrical Components": dict(share=0.14, gm=0.29, cost_up=0.075, list_up=0.030,
                                      vol_growth=0.040, avg_price=70.0, line_qty=19),
        "Tools & Equipment":     dict(share=0.18, gm=0.32, cost_up=0.010, list_up=0.000,
                                      vol_growth=0.080, avg_price=200.0, line_qty=8),
        "Safety & PPE":          dict(share=0.13, gm=0.34, cost_up=0.005, list_up=0.000,
                                      vol_growth=0.070, avg_price=45.0, line_qty=30),
        "Specialty Engineered":  dict(share=0.20, gm=0.37, cost_up=0.000, list_up=0.000,
                                      vol_growth=-0.010, avg_price=600.0, line_qty=4),
    },

    # channel: share of PP net revenue, number of accounts, discount FY25 / FY26
    "channels": {
        "Distributor": dict(share=0.45, n=120, disc_fy25=0.120, disc_fy26=0.120),
        "Direct":      dict(share=0.35, n=130, disc_fy25=0.070, disc_fy26=0.072),
        "Online":      dict(share=0.20, n=150, disc_fy25=0.040, disc_fy26=0.042),
    },
    # the designed discount-pressure group: large distributor accounts whose
    # FY26 discount widens far beyond the channel drift
    "pressure_accounts": dict(n=12, channel="Distributor", rank_from=0, rank_to=11,
                              disc_fy26=0.222),

    "new_sku_cp_revenue_share": 0.0105,     # of PP net revenue
    "disc_sku_pp_revenue_share": 0.0055,
    "sku_weight_sigma": 0.80,
    "customer_weight_sigma": 1.05,
    "customer_discount_sigma": 0.12,
    "sku_growth_noise_sigma": 0.10,
    "month_noise_sigma": 0.07,
    "seasonality": [1.05, 1.02, 0.82, 0.90, 0.98, 1.12, 1.06, 1.04, 1.08, 0.92, 0.95, 1.06],
    "typical_discount_for_costing": 0.080,
    "sku_price_sigma": 0.80,
    "line_split_alpha": 0.65,
    # invoice baskets: lines for one customer in one month are grouped into
    # orders instead of being scattered across distinct days. Economically
    # neutral - it changes only the day within the month, drawn from a
    # separate RNG so the main stream (quantities, customers, discounts) is
    # untouched. Set to 0 to disable and keep one line per invoice.
    "basket_target_lines": 4.0,
    "basket_rng_offset": 1,
    # within-category margin-rate dispersion and the growth tilt against it.
    # Both 0 reproduces the flat-margin behaviour of runs 1-5.
    "sku_gm_sigma": 0.05,
    "within_category_margin_tilt": 0.035,
}

MONTH_ORDER = ["Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"]


def month_list(start, end):
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    out, y, m = [], s.year, s.month
    while (y, m) <= (e.year, e.month):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def build(params):
    p = params
    rng = np.random.default_rng(p["seed"])
    cats = p["categories"]
    cat_names = list(cats)
    R0 = p["target_pp_net_revenue_musd"] * 1_000_000
    disc_cost = p["typical_discount_for_costing"]

    # ---------------- product dimension -----------------------------------
    per_cat = p["n_skus"] // len(cat_names)
    prod = []
    for ci, cn in enumerate(cat_names):
        c = cats[cn]
        sigma = p["sku_price_sigma"]
        mu = np.log(c["avg_price"]) - sigma ** 2 / 2
        for j in range(per_cat):
            lp = float(np.round(rng.lognormal(mu, sigma), 2))
            lp = max(lp, 0.50)
            prod.append(dict(
                product_id=f"P{ci*per_cat + j + 1:04d}",
                product_name=f"{cn.split()[0][:4].upper()}-{j+1:03d}",
                category=cn,
                sub_category=f"{cn} / Group {chr(65 + j % 4)}",
                brand=f"Brand {chr(65 + (ci*7 + j) % 12)}",
                list_price_fy25=lp,
                std_cost_fy25=round(lp * (1 - disc_cost) * (1 - c["gm"]), 4),
                weight=float(rng.lognormal(0, p["sku_weight_sigma"])),
            ))
    prod = pd.DataFrame(prod)
    # per-SKU gross-margin dispersion inside each category, then renormalised
    # so the category's weight-average margin rate is exactly the designed one
    prod["gm_sku"] = prod.category.map(lambda x: cats[x]["gm"])
    if p["sku_gm_sigma"] > 0:
        eps = rng.normal(0, p["sku_gm_sigma"], len(prod))
        prod["gm_sku"] = np.clip(prod.gm_sku + eps, 0.04, 0.60)
        for cn in cat_names:
            m = (prod.category == cn).to_numpy()
            w = prod.loc[m, "weight"].to_numpy()
            w = w / w.sum()
            prod.loc[m, "gm_sku"] = np.clip(
                prod.loc[m, "gm_sku"].to_numpy() - ((w * prod.loc[m, "gm_sku"]).sum() - cats[cn]["gm"]),
                0.04, 0.60)
        prod["std_cost_fy25"] = np.round(prod.list_price_fy25 * (1 - disc_cost) * (1 - prod.gm_sku), 4)

    prod["list_price_fy26"] = np.round(
        prod.list_price_fy25 * (1 + prod.category.map(lambda x: cats[x]["list_up"])), 2)
    prod["std_cost_fy26"] = np.round(
        prod.std_cost_fy25 * (1 + prod.category.map(lambda x: cats[x]["cost_up"])), 4)

    # entry / exit: smallest-weight SKUs become new or discontinued lines
    order = prod.weight.argsort().to_numpy()
    tail = list(order[: p["n_new_skus"] + p["n_discontinued_skus"] + 40])
    rng.shuffle(tail)
    new_idx = tail[: p["n_new_skus"]]
    exit_idx = tail[p["n_new_skus"]: p["n_new_skus"] + p["n_discontinued_skus"]]

    prod["launch_month"] = pd.NaT
    prod["discontinued_month"] = pd.NaT
    cp_months = month_list(p["cp_start"], p["cp_end"])
    pp_months = month_list(p["pp_start"], p["pp_end"])
    launch = {}
    stop = {}
    for i in new_idx:
        y, m = cp_months[int(rng.integers(0, 6))]
        launch[prod.product_id.iloc[i]] = (y, m)
        prod.iloc[i, prod.columns.get_loc("launch_month")] = f"{y}-{m:02d}"
    for i in exit_idx:
        y, m = pp_months[int(rng.integers(5, 12))]
        stop[prod.product_id.iloc[i]] = (y, m)
        prod.iloc[i, prod.columns.get_loc("discontinued_month")] = f"{y}-{m:02d}"

    prod["lifecycle"] = np.where(prod.product_id.isin(launch), "New in CP",
                         np.where(prod.product_id.isin(stop), "Discontinued in PP", "Continuing"))

    # ---------------- revenue allocation ----------------------------------
    new_rev_total = R0 * p["new_sku_cp_revenue_share"]
    exit_rev_total = R0 * p["disc_sku_pp_revenue_share"]

    prod["pp_revenue_target"] = 0.0
    prod["cp_revenue_target"] = 0.0
    for cn in cat_names:
        m = (prod.category == cn) & (prod.lifecycle == "Continuing")
        w = prod.loc[m, "weight"]
        prod.loc[m, "pp_revenue_target"] = R0 * cats[cn]["share"] * (w / w.sum())
    # new / discontinued carry their own small, explicit allocation
    for ids, total, col in ((launch, new_rev_total, "cp_revenue_target"),
                            (stop, exit_rev_total, "pp_revenue_target")):
        m = prod.product_id.isin(ids)
        w = prod.loc[m, "weight"]
        prod.loc[m, col] = total * (w / w.sum()).to_numpy()

    # continuing SKUs: CP units = PP units * category growth * SKU noise,
    # renormalised so the *designed category growth* is what it says it is
    noise = rng.lognormal(0, p["sku_growth_noise_sigma"], len(prod))
    prod["growth_factor"] = 1.0
    for cn in cat_names:
        m = ((prod.category == cn) & (prod.lifecycle == "Continuing")).to_numpy()
        g = 1 + cats[cn]["vol_growth"]
        f = noise[m]
        if p["within_category_margin_tilt"] > 0:
            gm_c = prod.loc[m, "gm_sku"].to_numpy()
            z = (gm_c - gm_c.mean()) / (gm_c.std() if gm_c.std() > 0 else 1.0)
            f = f * np.exp(-p["within_category_margin_tilt"] * z)
        base_units = (prod.loc[m, "pp_revenue_target"] /
                      (prod.loc[m, "list_price_fy25"] * (1 - disc_cost))).to_numpy()
        scale = base_units.sum() * g / (base_units * f).sum()
        prod.loc[m, "growth_factor"] = f * scale

    prod["pp_units"] = np.where(
        prod.pp_revenue_target > 0,
        prod.pp_revenue_target / (prod.list_price_fy25 * (1 - disc_cost)), 0.0)
    prod["cp_units"] = np.where(
        prod.lifecycle == "New in CP",
        prod.cp_revenue_target / (prod.list_price_fy26 * (1 - disc_cost)),
        np.where(prod.lifecycle == "Continuing", prod.pp_units * prod.growth_factor, 0.0))

    # ---------------- customer dimension ----------------------------------
    cust = []
    cid = 1
    for ch, cc in p["channels"].items():
        w = rng.lognormal(0, p["customer_weight_sigma"], cc["n"])
        w = w / w.sum() * cc["share"]
        for k in range(cc["n"]):
            cust.append(dict(customer_id=f"C{cid:04d}",
                             customer_name=f"{ch[:3].upper()} Account {cid:04d}",
                             channel=ch, region=["North", "South", "Midlands", "Export"][cid % 4],
                             weight=float(w[k]),
                             disc_factor=float(rng.lognormal(0, p["customer_discount_sigma"]))))
            cid += 1
    cust = pd.DataFrame(cust)

    # designed discount-pressure group: large distributor accounts, not the top one
    pa = p["pressure_accounts"]
    dist = cust[cust.channel == pa["channel"]].sort_values("weight", ascending=False)
    pressure = list(dist.customer_id.iloc[pa["rank_from"]: pa["rank_to"] + 1][: pa["n"]])
    cust["discount_pressure"] = cust.customer_id.isin(pressure)

    # segment and size band frozen on PP revenue weight, as the data contract says
    q = cust.weight.rank(pct=True)
    cust["segment"] = np.where(q > 0.90, "Key", np.where(q > 0.55, "Mid", "Tail"))
    cust["size_band"] = pd.cut(q, [0, .25, .5, .75, .9, 1.0],
                               labels=["B5", "B4", "B3", "B2", "B1"]).astype(str)

    # ---------------- invoice lines ---------------------------------------
    seas = np.array(p["seasonality"], dtype=float)
    cw = cust.weight.to_numpy() / cust.weight.sum()
    cust_ids = cust.customer_id.to_numpy()
    cust_ch = dict(zip(cust.customer_id, cust.channel))
    cust_df = dict(zip(cust.customer_id, cust.disc_factor))
    press = set(pressure)

    rows = []
    for per, months, ucol, lcol, ccol in (
            ("PP", pp_months, "pp_units", "list_price_fy25", "std_cost_fy25"),
            ("CP", cp_months, "cp_units", "list_price_fy26", "std_cost_fy26")):
        seas_y = seas * (1 + rng.normal(0, 0.02, 12))
        seas_y = seas_y / seas_y.mean()
        for pi in range(len(prod)):
            units_year = prod[ucol].iloc[pi]
            if units_year <= 0:
                continue
            pid = prod.product_id.iloc[pi]
            cat = prod.category.iloc[pi]
            lp = prod[lcol].iloc[pi]
            sc = prod[ccol].iloc[pi]
            qline = cats[cat]["line_qty"]
            active = []
            for mi, (y, m) in enumerate(months):
                if pid in launch and (y, m) < launch[pid]:
                    continue
                if pid in stop and (y, m) > stop[pid]:
                    continue
                active.append((mi, y, m))
            if not active:
                continue
            w_active = np.array([seas_y[mi] for mi, _, _ in active])
            w_active = w_active / w_active.sum()
            for (mi, y, m), share in zip(active, w_active):
                u = units_year * share * np.exp(rng.normal(0, p["month_noise_sigma"]))
                n_lines = max(1, int(round(u / qline)))
                if n_lines > 60:
                    n_lines = 60
                parts = rng.dirichlet(np.full(n_lines, p["line_split_alpha"]))
                qs = np.maximum(1, np.round(u * parts).astype(int))
                days = calendar.monthrange(y, m)[1]
                for qi in qs:
                    cidx = rng.choice(len(cust_ids), p=cw)
                    cu = cust_ids[cidx]
                    ch = cust_ch[cu]
                    base = p["channels"][ch]["disc_fy25" if per == "PP" else "disc_fy26"]
                    if per == "CP" and cu in press:
                        base = pa["disc_fy26"]
                    d = base * cust_df[cu] * np.exp(rng.normal(0, 0.05))
                    d = float(np.clip(d, 0.0, 0.45))
                    day = int(rng.integers(1, days + 1))
                    gross = round(float(qi) * lp, 2)
                    disc = round(gross * d, 2)
                    rows.append((dt.date(y, m, day).isoformat(), cu, pid, int(qi),
                                 gross, disc, round(gross - disc, 2), sc,
                                 round(float(qi) * sc, 2), per))

    fact = pd.DataFrame(rows, columns=["invoice_date", "customer_id", "product_id",
                                       "quantity", "gross_revenue", "discount_amount",
                                       "net_revenue", "unit_standard_cost", "total_cost",
                                       "period"])
    # invoice baskets. A customer buying several SKUs in a month places a few
    # orders, not one order per line. Re-date each line onto one of its
    # customer-month order days, keeping the month (so period, seasonality,
    # prices and costs are all unchanged) and using a separate RNG so no
    # economic value in the file moves.
    if p.get("basket_target_lines", 0):
        rng2 = np.random.default_rng(p["seed"] + p["basket_rng_offset"])
        fact["_ym"] = fact.invoice_date.str[:7]
        newdate = fact.invoice_date.to_numpy().copy()
        for (cu, ym), idx in fact.groupby(["customer_id", "_ym"], sort=False).indices.items():
            n = len(idx)
            y, mo = int(ym[:4]), int(ym[5:7])
            dim = calendar.monthrange(y, mo)[1]
            k = max(1, int(round(n / p["basket_target_lines"])))
            k = min(k, dim)
            days_sel = rng2.choice(np.arange(1, dim + 1), size=k, replace=False)
            w = rng2.dirichlet(np.full(k, 2.0))
            pick = rng2.choice(k, size=n, p=w)
            for j, pos in enumerate(idx):
                newdate[pos] = dt.date(y, mo, int(days_sel[pick[j]])).isoformat()
        fact["invoice_date"] = newdate
        fact = fact.drop(columns=["_ym"])

    # invoices: lines sharing a customer and a date belong to one invoice
    fact = fact.sort_values(["invoice_date", "customer_id", "product_id"]).reset_index(drop=True)
    grp = fact.groupby(["invoice_date", "customer_id"], sort=False).ngroup()
    fact["invoice_no"] = ["INV-" + f"{g + 100000:06d}" for g in grp]
    fact["line_no"] = fact.groupby("invoice_no").cumcount() + 1
    fact = fact[["invoice_date", "invoice_no", "line_no", "customer_id", "product_id",
                 "quantity", "gross_revenue", "discount_amount", "net_revenue",
                 "unit_standard_cost", "total_cost", "period"]]

    # ---------------- date dimension --------------------------------------
    days = pd.date_range(p["pp_start"], p["cp_end"], freq="D")
    dim_date = pd.DataFrame({"date": days.strftime("%Y-%m-%d")})
    dim_date["year"] = days.year
    dim_date["quarter"] = "Q" + days.quarter.astype(str)
    dim_date["month_no"] = days.month
    dim_date["month_name"] = days.strftime("%b")
    dim_date["year_month"] = days.strftime("%Y-%m")
    dim_date["day_of_month"] = days.day
    dim_date["is_month_end"] = days.is_month_end
    dim_date["comparison_period"] = np.where(days < pd.Timestamp(p["cp_start"]), "PP", "CP")
    dim_date["cost_year"] = np.where(days < pd.Timestamp(p["cp_start"]), "FY25", "FY26")
    dim_date["prior_year_date"] = (days - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    dim_date["months_from_cp_end"] = (
        (pd.Timestamp(p["cp_end"]).year - days.year) * 12
        + pd.Timestamp(p["cp_end"]).month - days.month)

    # ---------------- write -----------------------------------------------
    DATA.mkdir(exist_ok=True, parents=True)
    prod_out = prod[["product_id", "product_name", "category", "sub_category", "brand",
                     "list_price_fy25", "list_price_fy26", "std_cost_fy25", "std_cost_fy26",
                     "launch_month", "discontinued_month", "lifecycle"]]
    cust_out = cust[["customer_id", "customer_name", "channel", "segment", "size_band",
                     "region", "discount_pressure"]]
    files = {
        "fact_sales_line.csv": fact,
        "dim_product.csv": prod_out,
        "dim_customer.csv": cust_out,
        "dim_date.csv": dim_date,
    }
    manifest = {"params": p, "files": {}}
    for name, df in files.items():
        path = DATA / name
        df.to_csv(path, index=False)
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest["files"][name] = dict(rows=int(len(df)), cols=int(df.shape[1]),
                                       sha256=h, bytes=path.stat().st_size)
    import sys
    manifest["environment"] = dict(python=sys.version.split()[0],
                                   numpy=np.__version__, pandas=pd.__version__)
    REPORTS.mkdir(exist_ok=True, parents=True)
    (REPORTS / f"manifest_{p['run_label']}.json").write_text(json.dumps(manifest, indent=2))
    return fact, prod_out, cust_out, dim_date, manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int)
    ap.add_argument("--run-label")
    ap.add_argument("--params")
    a = ap.parse_args()
    pr = dict(PARAMS)
    if a.params:
        pr.update(json.loads(Path(a.params).read_text()))
    if a.seed:
        pr["seed"] = a.seed
    if a.run_label:
        pr["run_label"] = a.run_label
    f, pd_, cu, dd, man = build(pr)
    print(f"run {pr['run_label']} seed {pr['seed']}")
    for k, v in man["files"].items():
        print(f"  {k:24s} {v['rows']:>7,} rows  sha256 {v['sha256'][:16]}...")
    print(f"  PP net revenue  {f[f.period=='PP'].net_revenue.sum():>15,.2f}")
    print(f"  CP net revenue  {f[f.period=='CP'].net_revenue.sum():>15,.2f}")
