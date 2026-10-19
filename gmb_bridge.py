"""
Gross Margin Bridge - approved methodology (Phase 1, audited 10 Oct 2026).

This module is the single definition of the bridge. It is copied verbatim from
the function that passed the 46-test Phase 1 audit and MUST NOT be changed to
make a Phase 2 scenario target pass. Any change here invalidates the audit.

Convention: symmetric midpoint (Bennet) decomposition at SKU grain with a
midpoint-price-weighted volume index, and two explicit range steps.

    Qbar = (Q0+Q1)/2   Pbar = (P0+P1)/2   M = P-C   Mbar = (M0+M1)/2
    gamma  = sum(Pbar*dQ) / sum(Pbar*Qbar)        over the comparable population
    Price  = sum( Qbar * (P1-P0) )
    Cost   = sum( -Qbar * (C1-C0) )
    Volume = sum( Mbar * Qbar * gamma )
    Mix    = sum( Mbar * (dQ - Qbar*gamma) )
    New    = sum( Q1*M1 )  over SKUs sold only in the current period
    Disc   = sum( -Q0*M0 ) over SKUs sold only in the prior period
    Residual = dGP - (Price+Cost+Volume+Mix+New+Disc)  == 0 by construction

Populations are disjoint: comparable {Q0>0 and Q1>0}, new {Q0=0 and Q1>0},
discontinued {Q0>0 and Q1=0}. New and discontinued SKUs are excluded from gamma.
Zero denominator (sum(Pbar*Qbar) == 0) defines gamma = 0.
"""
import numpy as np


def tol(gp_pp):
    """Approved hard-failure tolerance: max($1.00, 1e-9 * |GP prior period|)."""
    return max(1.00, 1e-9 * abs(gp_pp))


def bridge(sku, gamma_override=None, index="midpoint"):
    """sku: DataFrame with q0,p0,c0,q1,p1,c1. Absent period => q=0, p/c = NaN."""
    d = sku.copy()
    comp = (d.q0 > 0) & (d.q1 > 0)
    new = (d.q0 == 0) & (d.q1 > 0)
    exi = (d.q0 > 0) & (d.q1 == 0)

    c = d[comp]
    qbar = (c.q0 + c.q1) / 2
    pbar = (c.p0 + c.p1) / 2
    m0, m1 = c.p0 - c.c0, c.p1 - c.c1
    mbar = (m0 + m1) / 2
    dq = c.q1 - c.q0

    price = (qbar * (c.p1 - c.p0)).sum()
    cost = (-qbar * (c.c1 - c.c0)).sum()

    if gamma_override is not None:
        gamma = gamma_override
    elif index == "midpoint":
        den = (pbar * qbar).sum()
        num = (pbar * dq).sum()
        gamma = 0.0 if den == 0 else num / den          # 0/0 -> 0 by rule
    elif index == "unit":
        den = qbar.sum()
        gamma = 0.0 if den == 0 else dq.sum() / den
    elif index == "laspeyres":                            # withdrawn, negative control only
        den = (c.q0 * c.p0).sum()
        gamma = 0.0 if den == 0 else (c.q1 * c.p0).sum() / den - 1

    if index == "laspeyres":
        vol_i = mbar * c.q0 * gamma
        mix_i = mbar * (dq - c.q0 * gamma)
    else:
        vol_i = mbar * qbar * gamma
        mix_i = mbar * (dq - qbar * gamma)

    n = d[new]
    x = d[exi]
    newv = (n.q1 * (n.p1 - n.c1)).sum()
    excl = (-x.q0 * (x.p0 - x.c0)).sum()

    gp0 = (d.q0 * (d.p0 - d.c0)).fillna(0).sum()
    gp1 = (d.q1 * (d.p1 - d.c1)).fillna(0).sum()
    comps = dict(price=price, cost=cost, volume=vol_i.sum(), mix=mix_i.sum(),
                 new=newv, disc=excl)
    resid = (gp1 - gp0) - sum(comps.values())
    return dict(gp0=gp0, gp1=gp1, dgp=gp1 - gp0, gamma=gamma, residual=resid,
                mix_i=mix_i, vol_i=vol_i, comp_index=c.index,
                n_comp=int(comp.sum()), n_new=int(new.sum()), n_exit=int(exi.sum()),
                **comps)


def price_subsplit(sku):
    """List-price and discount sub-effects. They sum exactly to Price Effect.

    Needs l0,l1 (midpoint list prices) alongside p0,p1. d = L - P (discount per unit).
    """
    c = sku[(sku.q0 > 0) & (sku.q1 > 0)]
    qbar = (c.q0 + c.q1) / 2
    list_eff = (qbar * (c.l1 - c.l0)).sum()
    d0, d1 = c.l0 - c.p0, c.l1 - c.p1
    disc_eff = (-qbar * (d1 - d0)).sum()
    return dict(list_effect=list_eff, discount_effect=disc_eff,
                total=list_eff + disc_eff)


def lines_to_sku(lines, entity="product_id", period_col="period"):
    """Collapse invoice lines to the SKU x period grain the bridge consumes.

    Realized price = net revenue / quantity; unit cost = total cost / quantity,
    both volume-weighted within the period, exactly as the metric dictionary says.
    """
    import pandas as pd
    g = (lines.groupby([period_col, entity])
         .agg(q=("quantity", "sum"), r=("net_revenue", "sum"), k=("total_cost", "sum"),
              gr=("gross_revenue", "sum"))
         .reset_index())
    out = {}
    for per, suf in (("PP", "0"), ("CP", "1")):
        s = g[g[period_col] == per].set_index(entity)
        out["q" + suf] = s.q
        out["p" + suf] = s.r / s.q
        out["c" + suf] = s.k / s.q
        out["l" + suf] = s.gr / s.q
    d = pd.DataFrame(out)
    return d.fillna({"q0": 0, "q1": 0})
