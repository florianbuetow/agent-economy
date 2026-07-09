# Leaderboard Improvements Design

## Summary

Improve the observatory leaderboard component with clearer labeling and more meaningful ranking criteria.

## Changes

### 1. Add "TOP AGENTS" title above tabs

A small uppercase label positioned above the Workers/Posters tab toggle, matching the existing monospace design language (`text-[9px] font-mono uppercase tracking-[1.5px] text-text-muted`).

### 2. Workers tab: rank by amount earned

- Change subtitle from "By Tasks Completed" to "By Amount Earned"
- The data already sorts by `total_earned` — this just makes the ranking criterion explicit
- No data flow changes needed

### 3. Posters tab: rank by spec excellence

- Change subtitle from "By Tasks Posted" to "By Spec Excellence"
- Show `★★★ percentage` as the primary right-aligned metric (e.g., "85% ★★★")
- Move `total_spent` to the secondary row alongside task count
- Change fetch from `fetchAgents("total_spent", "desc", 10)` to `fetchAgents("spec_quality", "desc", 10)`
- Backend already supports `sort_by=spec_quality`

## Files to modify

- `services/observatory/frontend/src/components/Leaderboard.tsx` — UI changes
- `services/observatory/frontend/src/hooks/useAgents.ts` — change posters sort field
