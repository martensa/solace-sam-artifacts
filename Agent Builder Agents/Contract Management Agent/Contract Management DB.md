# Contract Management Database

## Related Files

- **Agent:** [Contract Management Agent](Contract%20Management%20Agent.md)
  defines the Solace Agent Mesh agent that queries this database.
- **SQL:** [contract_management_db.sql](contract_management_db.sql)
  is the SQL file that creates and populates the database.

## Overview

PostgreSQL database for a **Solace Agent Mesh** demo showcasing AI-powered article recognition and market price comparison in facility management procurement.

**Use Case:** A facility manager searches for an article (by name, EAN, or free text). The system identifies the article, compares market prices (B2B price comparison), and checks framework contracts for potentially better pricing - including volume-based tiered pricing.

## Quick Start

```bash
# Option A: Docker (recommended) - place SQL file in initdb.d
docker run -d --name procurement-db \
  -e POSTGRES_PASSWORD=postgres \
  -v ./contract_management_db.sql:/docker-entrypoint-initdb.d/01_init.sql \
  -p 5432:5432 postgres:14

# Option B: Manual setup
psql -U postgres -f contract_management_db.sql
```

After loading, you will see a verification notice confirming all data loaded successfully.

The SQL file creates database `contract_management` and all objects live in the `contracts` schema:

```sql
\c contract_management
SET search_path TO contracts;
```

## Schema Overview

```text
contracts
  +-- categories            (5 rows)
  +-- suppliers             (15 rows)
  +-- articles              (50 rows, identified by EAN)
  +-- contracts             (15 framework agreements)
  +-- contract_articles     (150 line items linking contracts to articles)
  +-- tiered_pricing        (118 volume discount tiers)
```

### Entity Relationships

```text
suppliers 1---* contracts 1---* contract_articles *---1 articles *---1 categories
                                       |
                                tiered_pricing
```

## Data Summary

| Dimension | Count | Details |
|-----------|-------|---------|
| Categories | 5 | Office Supplies, HVAC, Electrical, Sanitary, Tools and Accessories |
| Suppliers | 15 | German B2B suppliers with full contact details |
| Articles | 50 | Real products with verifiable EAN-13 numbers |
| Contracts | 15 | Active framework agreements (2025-2027) |
| Contract Articles | 150 | Pricing across overlapping supplier portfolios |
| Tiered Pricing | 118 | Volume discounts for 30 key articles |

## Categories and Sample Brands

| Category | Articles | Key Brands |
|----------|----------|------------|
| Office Supplies | 10 | tesa, Post-it (3M), Leitz, STABILO, edding, Pritt, UHU, Tipp-Ex, BIC, Faber-Castell |
| HVAC | 10 | Danfoss, Oventrop, Honeywell, Grundfos, Viega, Wilo, Zehnder |
| Electrical | 10 | Busch-Jaeger, Hager, Brennenstuhl, OSRAM, Philips, WAGO |
| Sanitary | 10 | Grohe, Hansgrohe, Geberit, WENKO |
| Tools and Accessories | 10 | Knipex, Wera, Stanley, Fischer, Bosch, Brennenstuhl |

## Built-in Views

| View | Purpose |
|------|---------|
| `v_best_contract_price` | Best price per article across all active contracts (ranked) |
| `v_best_tiered_price` | All tiered pricing options for quantity-based comparison |
| `v_contract_overview` | Contract summary with article counts and avg discount |
| `v_article_search` | Article catalog with best prices and savings potential |
| `v_supplier_article_matrix` | Full supplier-article cross-reference |

## Built-in Functions

### `search_articles(search_term)`

Fuzzy article search by name, EAN, brand, or description with relevance scoring.

```sql
SELECT * FROM search_articles('Grohe');
SELECT * FROM search_articles('4005176934520');  -- by EAN
SELECT * FROM search_articles('thermostatic');
```

### `get_best_price(article_id, quantity)`

Find the best available price for a given article and quantity, considering tiered pricing.

```sql
-- Best price for 1x Knipex Cobra
SELECT * FROM get_best_price(41, 1);

-- Best price for 50x Fischer DuoPower 8x40 (triggers volume tiers)
SELECT * FROM get_best_price(47, 50);

-- Best price for 100x OSRAM LED bulbs
SELECT * FROM get_best_price(25, 100);
```

### `recommend_order(article_id, quantity)`

Smart order recommendation: checks if ordering slightly more units unlocks a cheaper volume tier.

```sql
-- I need 8 Danfoss valves - should I order 10 instead?
SELECT * FROM recommend_order(11, 8);

-- I need 3 Grohe Eurosmart mixers - is there a better tier?
SELECT * FROM recommend_order(31, 3);
```

## Demo Scenarios

### Scenario 1: Single Article Lookup

A facility manager needs a Grohe Eurosmart basin mixer.

```sql
-- Find the article
SELECT * FROM search_articles('Eurosmart');

-- Compare all contract prices
SELECT * FROM v_best_contract_price WHERE article_id = 31 ORDER BY price_rank;

-- Best single-unit price
SELECT * FROM get_best_price(31, 1);
```

Result: 4 suppliers offer this item. SanProfi has the best price at 79.90 EUR vs. 119.00 EUR list price (33% savings).

### Scenario 2: Volume Order with Tiered Pricing

Ordering 50 Danfoss thermostatic valves for a building retrofit.

```sql
SELECT * FROM get_best_price(11, 50);
```

Result: KlimaTech offers 21.50 EUR/unit at 50+ quantity (vs. 38.50 EUR list = 44% savings). HausTechnik Mueller offers 22.90 EUR/unit.

### Scenario 3: Article Without Framework Contract

An item is requested that exists only in a single contract - market price comparison becomes essential.

```sql
-- Brennenstuhl Solar LED (only in contract 3)
SELECT * FROM get_best_price(28, 1);
-- Only 1 result: compare with online market price
```

### Scenario 4: Office Bulk Order

Ordering 500 BIC pens for the entire office.

```sql
SELECT * FROM get_best_price(9, 500);
```

Result: ProOffice Solutions offers 0.12 EUR/unit at 500+ (vs. 0.45 EUR list = 73% savings). Office Direct offers 0.15 EUR/unit.

### Scenario 5: Cross-Category Comparison

Compare a Grohe Eurosmart basin mixer across suppliers and find the optimal tier.

```sql
SELECT supplier_name, contract_number, unit_price, total_price,
       savings_vs_list, delivery_days, price_type
FROM get_best_price(31, 25);
```

Result: SanProfi tiered pricing kicks in at 25 units = 64.90 EUR/unit. FacilityPro offers 69.90 EUR at the same tier. Both beat the 119.00 EUR list price significantly.

### Scenario 6: Smart Order Recommendation

A facility manager needs 8 Danfoss thermostatic valves. Should they order 10 to unlock a better tier?

```sql
SELECT * FROM recommend_order(11, 8);
```

Result: ordering 10 instead of 8 unlocks the next volume tier at multiple suppliers, reducing unit cost.

## Technical Notes

- All prices in EUR
- Contract prices are always below list/market prices
- Tiered pricing provides additional savings beyond base contract price
- Some articles overlap across multiple suppliers (competitive pricing)
- Some articles are exclusive to a single supplier (sole source)
- All EAN-13 numbers are based on real product identifiers
- All content is in English with no special characters
- Database: `contract_management`, Schema: `contracts`
- All tables, views, and functions have PostgreSQL COMMENT metadata for LLM agent introspection
- Version: 1.0
