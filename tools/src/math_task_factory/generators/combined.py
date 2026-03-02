"""Level 15: Combined difficulty generators."""

from __future__ import annotations

import random

from math_task_factory.types import MathTask


def gen_combined_chain_constraint(rng: random.Random) -> MathTask:
    """Multi-step arithmetic where intermediate result determines which constraint to check."""
    start = rng.randint(100, 500)
    ops = []
    total = start

    # 3-4 arithmetic operations
    num_ops = rng.randint(3, 4)
    for _ in range(num_ops):
        op = rng.choice(["add", "multiply"])
        if op == "add":
            val = rng.randint(20, 200)
            total += val
            ops.append(f"Add {val}.")
        else:
            val = rng.randint(2, 5)
            total *= val
            ops.append(f"Multiply by {val}.")

    # Constraint check based on intermediate
    threshold = rng.randint(500, 2000)
    mod_a = rng.randint(7, 19)
    mod_b = rng.randint(11, 23)

    if total > threshold:
        chosen_mod = mod_a
        branch = f"the result exceeds {threshold}"
        instruction = f"take modulo {mod_a}"
    else:
        chosen_mod = mod_b
        branch = f"the result is {threshold} or less"
        instruction = f"take modulo {mod_b}"

    after_mod = total % chosen_mod

    # Final chain
    final_mult = rng.randint(3, 12)
    final_add = rng.randint(10, 100)
    result = after_mod * final_mult + final_add

    ops_text = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(ops))
    return MathTask(
        title="Combined chain + constraint",
        spec=f"""TASK: Perform the following computation:

Phase 1 — Chain arithmetic (starting with {start}):
{ops_text}

Phase 2 — Constraint check:
  - If {branch}, {instruction}.
  - Otherwise, take modulo {mod_b if total > threshold else mod_a}.

Phase 3 — Final computation:
  - Multiply the Phase 2 result by {final_mult}.
  - Add {final_add}.

What is the final result?

OUTPUT FORMAT: A single integer.

VERIFICATION:
  - After Phase 1: {total}
  - Phase 2 ({branch}): {total} mod {chosen_mod} = {after_mod}
  - Phase 3: {after_mod} * {final_mult} + {final_add} = {result}""",
        solutions=[str(result)],
        level=15,
        problem_type="combined_chain_constraint",
    )


def gen_combined_state_large(rng: random.Random) -> MathTask:
    """State tracking problem with large numbers (5-digit per entity, 5+ entities)."""
    entity_names = ["Warehouse A", "Warehouse B", "Warehouse C",
                    "Warehouse D", "Warehouse E"]
    num_entities = rng.randint(5, 5)
    entities = entity_names[:num_entities]
    state = {e: rng.randint(10000, 99999) for e in entities}

    num_ops = rng.randint(5, 7)
    ops_text: list[str] = []
    initial_text = ", ".join(f"{e}: {state[e]}" for e in entities)

    for _ in range(num_ops):
        op = rng.choice(["add", "subtract", "transfer", "percentage"])
        if op == "add":
            entity = rng.choice(entities)
            val = rng.randint(1000, 20000)
            state[entity] += val
            ops_text.append(f"Add {val} to {entity}.")
        elif op == "subtract":
            entity = rng.choice(entities)
            max_sub = min(15000, state[entity] - 1000)
            if max_sub < 1000:
                # Not enough to subtract; add instead
                val = rng.randint(1000, 5000)
                state[entity] += val
                ops_text.append(f"Add {val} to {entity}.")
                continue
            val = rng.randint(1000, max_sub)
            state[entity] -= val
            ops_text.append(f"Remove {val} from {entity}.")
        elif op == "transfer":
            src, dst = rng.sample(entities, 2)
            max_transfer = min(10000, state[src] - 1000)
            if max_transfer < 1000:
                val = rng.randint(1000, 5000)
                state[dst] += val
                ops_text.append(f"Add {val} to {dst}.")
                continue
            val = rng.randint(1000, max_transfer)
            state[src] -= val
            state[dst] += val
            ops_text.append(f"Transfer {val} from {src} to {dst}.")
        else:
            entity = rng.choice(entities)
            pct = rng.choice([5, 10, 15, 20])
            change = state[entity] * pct // 100
            state[entity] += change
            ops_text.append(
                f"Increase {entity} by {pct}% (integer division for the increase amount)."
            )

    # Ask for total or specific entity
    if rng.random() < 0.4:
        answer = sum(state.values())
        question = "What is the TOTAL across all warehouses?"
    else:
        target = rng.choice(entities)
        answer = state[target]
        question = f"What is the final count at {target}?"

    ops_numbered = "\n".join(f"  {i+1}. {o}" for i, o in enumerate(ops_text))
    return MathTask(
        title="Large-scale state tracking",
        spec=f"""TASK: There are {num_entities} warehouses with initial stock:
  {initial_text}

The following operations occur in order:
{ops_numbered}

{question}

OUTPUT FORMAT: A single integer.

VERIFICATION: Track each warehouse's count through every operation, using integer
division where percentage calculations are involved.""",
        solutions=[str(answer)],
        level=15,
        problem_type="combined_state_large",
    )


def gen_combined_conditional_chain(rng: random.Random) -> MathTask:
    """Branching logic + chained arithmetic where branch depends on intermediate results."""
    values = [rng.randint(10, 100) for _ in range(3)]
    a, b, c = values

    # Phase 1: compute an intermediate
    intermediate_1 = a * b + c
    threshold_1 = rng.randint(200, 800)

    # Phase 2: branch based on intermediate_1
    add_val = rng.randint(50, 300)
    sub_val = rng.randint(20, 150)
    if intermediate_1 > threshold_1:
        phase_2_result = intermediate_1 + add_val
        branch_1 = "greater"
    else:
        phase_2_result = intermediate_1 - sub_val
        branch_1 = "not greater"

    # Phase 3: another chain
    mult = rng.randint(2, 6)
    phase_3_result = phase_2_result * mult

    # Phase 4: second branch
    threshold_2 = rng.randint(1000, 5000)
    mod_val = rng.randint(13, 37)
    div_val = rng.choice([2, 3, 4, 5])

    if phase_3_result > threshold_2:
        result = phase_3_result % mod_val
        branch_2 = "greater"
        branch_2_op = f"modulo {mod_val}"
    else:
        result = phase_3_result // div_val
        branch_2 = "not greater"
        branch_2_op = f"integer division by {div_val}"

    return MathTask(
        title="Multi-branch conditional chain",
        spec=f"""TASK: Perform the following multi-phase computation:

Phase 1: Compute {a} * {b} + {c}.

Phase 2: Check the Phase 1 result:
  - If GREATER than {threshold_1}: add {add_val}.
  - If {threshold_1} or less: subtract {sub_val}.

Phase 3: Multiply the Phase 2 result by {mult}.

Phase 4: Check the Phase 3 result:
  - If GREATER than {threshold_2}: take modulo {mod_val}.
  - If {threshold_2} or less: integer-divide by {div_val}.

What is the final result?

OUTPUT FORMAT: A single integer.

VERIFICATION:
  - Phase 1: {a} * {b} + {c} = {intermediate_1}
  - Phase 2: {intermediate_1} is {branch_1} than {threshold_1} → {phase_2_result}
  - Phase 3: {phase_2_result} * {mult} = {phase_3_result}
  - Phase 4: {phase_3_result} is {branch_2} than {threshold_2} → {branch_2_op} = {result}""",
        solutions=[str(result)],
        level=15,
        problem_type="combined_conditional_chain",
    )
