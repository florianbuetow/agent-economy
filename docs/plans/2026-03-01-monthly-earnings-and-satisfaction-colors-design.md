# Monthly Earnings Chart + Satisfaction Color Fix

## 1. Monthly Earnings Bar Chart

**Placement:** Left panel (ReputationPanel), directly below the existing cumulative EarningsChart, with a "MONTHLY EARNINGS" section header.

**Chart type:** Vertical bar chart using Chart.js (already a dependency).

**Data source:** Derive from the existing `earnings.data_points` array — group by month (year-month), sum the deltas between consecutive cumulative values to get per-month totals. No backend changes needed.

**Visual style:**
- Bars use `--color-accent`
- Hover tooltip shows month name + exact amount
- X-axis: abbreviated month labels (e.g., "Jan", "Feb")
- Y-axis: amounts in "k" format
- Same responsive sizing as existing EarningsChart

## 2. Satisfaction Chart Color Fix

**Current problem:** Both "Extremely Satisfied" (3 stars) and "Satisfied" (2 stars) use the same `--color-green`.

**Fix:** Change "Satisfied" (2 stars) bar to yellow/amber. Add `--color-yellow` CSS variable to all three themes. Update QualitySection component to use it for the middle rating.

**Colors across themes:**
- Extremely Satisfied (3 stars): Green (unchanged)
- Satisfied (2 stars): Yellow/amber (new)
- Dissatisfied (1 star): Red (unchanged)
