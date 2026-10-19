# Gross Margin Bridge — Data Dictionary

Dataset: synthetic. Run `run7-accepted`, seed `20261010`, generated 10 Oct 2026 by `gmb_generate.py`.
Currency USD. Grain, units and sign conventions below are binding on every downstream calculation.

**Disclosure.** Every row is synthetic and was generated to demonstrate a designed commercial
scenario. No figure describes a real business. Costs are *standard* costs revised annually, not
actual purchase costs. Returns, credit notes, rebates, settlement discounts, tax and shipping are
excluded by design.

---

## 1. `fact_sales_line.csv` — 72,511 rows

Grain: one row per invoice line. Primary key `(invoice_no, line_no)`.

| Field | Type | Definition | Constraint |
| --- | --- | --- | --- |
| `invoice_date` | date, ISO | Date of invoice. FK → `dim_date.date` | 2024-10-01 … 2026-09-30 |
| `invoice_no` | string | Invoice identifier, `INV-NNNNNN`. One invoice = one customer, one date. Lines for a customer within a month are grouped into orders, averaging 3.99 lines per invoice | not null |
| `line_no` | int | Line sequence within the invoice | ≥ 1, gapless from 1 |
| `customer_id` | string | FK → `dim_customer.customer_id` | resolves |
| `product_id` | string | FK → `dim_product.product_id` | resolves |
| `quantity` | int, units | Units sold on the line | strictly > 0 |
| `gross_revenue` | decimal(2), USD | `quantity × list price in force at invoice_date` | > 0 |
| `discount_amount` | decimal(2), USD | `gross_revenue − net_revenue`. Positive means a discount given | ≥ 0, < gross_revenue |
| `net_revenue` | decimal(2), USD | Realized invoiced revenue. The revenue base for all margin work | > 0 |
| `unit_standard_cost` | decimal(4), USD/unit | Standard cost in force at `invoice_date` | > 0, one value per SKU per cost year |
| `total_cost` | decimal(2), USD | `quantity × unit_standard_cost` | > 0 |
| `period` | string | `PP` or `CP`. Denormalised convenience column; it agrees with `dim_date.comparison_period` row for row | PP \| CP |

Derived, not stored: gross profit = `net_revenue − total_cost`; realized unit price =
`net_revenue ÷ quantity`; list unit price = `gross_revenue ÷ quantity`.

## 2. `dim_product.csv` — 300 rows

Grain: one row per SKU. Primary key `product_id`.

| Field | Type | Definition |
| --- | --- | --- |
| `product_id` | string | Primary key, `PNNNN` |
| `product_name` | string | Display name |
| `category` | string | One of six categories |
| `sub_category` | string | Grouping within category; reserve peer level if a category proves too broad |
| `brand` | string | Supplier brand |
| `list_price_fy25` | decimal(2) | Published list price, in force 2024-10-01 … 2025-09-30 |
| `list_price_fy26` | decimal(2) | Published list price, in force from 2025-10-01 |
| `std_cost_fy25` | decimal(4) | Standard cost, in force 2024-10-01 … 2025-09-30. Gross-margin rate varies across SKUs within a category (standard deviation 4.5–6.0 points, overall range 8.2%–47.8%), renormalised so each category's weight-average rate is the designed one |
| `std_cost_fy26` | decimal(4) | Standard cost, in force from 2025-10-01. Revisions are effective 1 October |
| `launch_month` | `YYYY-MM` or blank | First month the SKU may be sold. Populated for new SKUs only |
| `discontinued_month` | `YYYY-MM` or blank | Last month the SKU may be sold. Populated for withdrawn SKUs only |
| `lifecycle` | string | `Continuing` (255), `New in CP` (25), `Discontinued in PP` (20) |

The four price and cost columns are reference attributes. The bridge reads prices and costs from the
fact line, never from this table; the columns exist so the effective-dating can be audited.

## 3. `dim_customer.csv` — 400 rows

Grain: one row per account. Primary key `customer_id`.

| Field | Type | Definition |
| --- | --- | --- |
| `customer_id` | string | Primary key, `CNNNN` |
| `customer_name` | string | Display name |
| `channel` | string | `Distributor` (120), `Direct` (130), `Online` (150) |
| `segment` | string | `Key` / `Mid` / `Tail`, frozen on prior-period revenue so it cannot drift with the result |
| `size_band` | string | `B1`–`B5` peer band, frozen on the same basis |
| `region` | string | Geography. Context only; no analytical rule depends on it |
| `discount_pressure` | bool | True for the 12 accounts in the designed discount-widening group. **Generator provenance, not a client attribute** — it exists so the scenario can be audited and must not appear on the dashboard |

## 4. `dim_date.csv` — 730 rows

Grain: one row per day, 2024-10-01 … 2026-09-30. Primary key `date`. Mark as the model date table.

| Field | Type | Definition |
| --- | --- | --- |
| `date` | date, ISO | Primary key |
| `year`, `quarter`, `month_no`, `month_name`, `year_month`, `day_of_month` | — | Calendar attributes |
| `is_month_end` | bool | Last calendar day of the month |
| `comparison_period` | string | `PP` = 2024-10-01…2025-09-30, `CP` = 2025-10-01…2026-09-30 |
| `cost_year` | string | `FY25` / `FY26`. Standard-cost and list-price revisions are effective 1 October, so each cost year coincides exactly with one comparison period |
| `prior_year_date` | date | Same calendar date one year earlier; supports the prior-year comparison |
| `months_from_cp_end` | int | 0 at Sep 2026, rising backwards. Drives the 12 / 6 / 3-month comparison windows |

## 5. Period selector — a model artifact, not a source file

The comparison-period selector is **not** shipped as a fifth CSV. It is a disconnected table created
inside the Power BI model in Phase 4, with three rows:

| `period_key` | `label` | `months` |
| --- | --- | --- |
| 1 | Last 12 months vs prior year | 12 |
| 2 | Last 6 months vs prior year | 6 |
| 3 | Last 3 months vs prior year | 3 |

It has no relationship to any other table. A measure reads the selected `months` value and uses
`dim_date.months_from_cp_end` to resolve the current and prior windows. It is documented here because
it is part of the approved design, and it is excluded from the source files because it carries no
business data.

## 6. Relationships

| From | To | Cardinality | Direction |
| --- | --- | --- | --- |
| `dim_date.date` | `fact_sales_line.invoice_date` | 1 → * | single |
| `dim_product.product_id` | `fact_sales_line.product_id` | 1 → * | single |
| `dim_customer.customer_id` | `fact_sales_line.customer_id` | 1 → * | single |

No bidirectional filters, no many-to-many, no inactive relationships.

## 7. Known limitations

1. **Synthetic.** Generated to demonstrate a designed scenario. Disclose on the dashboard.
2. **Standard cost, not actual cost.** The cost effect measures standard-cost revisions effective
   1 October. It cannot support a statement about supplier inflation.
3. **No returns or credit notes.** Quantities are strictly positive, so the comparable-population
   test is a clean `Q > 0` check. Real client data needs returns netted at line level first.
4. **No tax, freight, rebates, settlement discounts or overriders.** Revenue is invoice-level and
   pre-tax; realized price excludes off-invoice income, which in a real distributor can move margin
   by 100–300 bps.
5. **Invoice baskets are synthetic.** Lines for one customer in one month are assigned to a small
   number of order days (18,152 invoices, mean 3.99 lines, median 3, max 42). Basket *composition*
   is random rather than behavioural, so the file supports invoice-count eligibility rules but
   should not be used to study which products are bought together.
6. **Growth is tilted against margin rate inside each category.** Lower-margin SKUs grow faster
   than higher-margin ones within the same category (correlation about −0.25), which is what makes
   the within-category mix effect non-zero. It is a designed assumption, not an observed
   behaviour.
