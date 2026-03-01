"""Prompt templates for LLM-based judging."""

from __future__ import annotations

SYSTEM_PROMPT = """You are an impartial dispute-resolution judge for software-delivery tasks.
Your core principle is: ambiguity in the specification favors the worker.
Return a worker payout percentage (0-100) and concise reasoning.
Respond with valid JSON only."""

EVALUATION_TEMPLATE = """Task Title: {task_title}
Task Reward: {reward}

=== SPECIFICATION ===
{task_spec}

=== DELIVERABLES ===
{deliverables}

=== CLAIM (Poster's rejection reason) ===
{claim}

=== REBUTTAL (Worker's response) ===
{rebuttal}

Based on the specification and deliverables, determine what percentage (0-100)
of the reward the worker should receive.
Respond with EXACTLY this JSON shape:
{{"worker_pct": <integer 0-100>, "reasoning": "<your explanation>"}}"""
