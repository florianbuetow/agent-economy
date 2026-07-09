# System & Sequence Diagrams — Design Document

**Date:** 2026-02-28
**Status:** Approved

## Goal

Create a single Markdown document with Mermaid diagrams illustrating the Agent Task Economy backend system. One system overview diagram plus six core flow sequence diagrams, each accompanied by step-by-step text descriptions.

## Audience

Both developers (exact endpoints, HTTP methods, auth patterns) and stakeholders (business-level flow, actor roles).

## Deliverable

Single file: `docs/diagrams/system-sequence-diagrams.md`

## Diagrams

| # | Title | Type | Key Services |
|---|-------|------|-------------|
| 0 | System Overview | graph | All 5 services + Agent actor |
| 1 | Agent Registration & Funding | sequenceDiagram | Identity, Central Bank |
| 2 | Task Posting with Escrow | sequenceDiagram | Task Board, Identity, Central Bank |
| 3 | Bidding & Contract Formation | sequenceDiagram | Task Board, Identity |
| 4 | Happy Path: Delivery & Approval | sequenceDiagram | Task Board, Identity, Central Bank |
| 5 | Dispute Path: Court Resolution | sequenceDiagram | Task Board, Identity, Central Bank, Court, Reputation |
| 6 | Review Timeout: Auto-Approval | sequenceDiagram | Task Board, Central Bank |

## Style Conventions

- Participants named by role (Poster, Worker, Bidder) and service name
- Arrow labels: `HTTP_METHOD /endpoint` with brief payload context
- `Note` blocks for business context (e.g., "Funds are now locked")
- `rect` blocks to group logical phases
- Text description below each diagram lists numbered steps
