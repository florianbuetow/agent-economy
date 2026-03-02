"""Level 13: Compound word problem generators."""

from __future__ import annotations

import random

from math_task_factory.types import MathTask


def gen_multi_step_word(rng: random.Random) -> MathTask:
    """Chain 3-4 sub-computations in a narrative about a store or business."""
    num_items = rng.randint(100, 300)
    cost_per_item = rng.randint(5, 25)
    total_cost = num_items * cost_per_item

    sell_pct = rng.choice([50, 55, 60, 65, 70, 75])
    markup_pct = rng.choice([20, 25, 30, 40, 50])
    discount_pct = rng.choice([15, 20, 25, 30])

    items_sold_full = num_items * sell_pct // 100
    items_remaining = num_items - items_sold_full

    sell_price = cost_per_item * (100 + markup_pct) // 100
    revenue_full = items_sold_full * sell_price

    discount_price = sell_price * (100 - discount_pct) // 100
    revenue_discount = items_remaining * discount_price

    total_revenue = revenue_full + revenue_discount
    profit = total_revenue - total_cost

    return MathTask(
        title="Multi-step business word problem",
        spec=f"""TASK: A store buys {num_items} items at ${cost_per_item} each.

Step 1: They sell {sell_pct}% of the items (using integer division for the count)
at a {markup_pct}% markup over cost (compute marked-up price using integer division).

Step 2: The remaining items are sold at a {discount_pct}% discount off the
marked-up price (compute discounted price using integer division).

Step 3: Calculate total revenue (full-price sales + discounted sales) minus
total cost.

What is the profit (or loss)?

OUTPUT FORMAT: A single integer (positive for profit, negative for loss).

VERIFICATION:
  - Total cost = {num_items} * {cost_per_item} = {total_cost}
  - Items at full price = {num_items} * {sell_pct} // 100 = {items_sold_full}
  - Remaining items = {items_remaining}
  - Marked-up price = {cost_per_item} * {100 + markup_pct} // 100 = {sell_price}
  - Revenue (full) = {items_sold_full} * {sell_price} = {revenue_full}
  - Discount price = {sell_price} * {100 - discount_pct} // 100 = {discount_price}
  - Revenue (discount) = {items_remaining} * {discount_price} = {revenue_discount}
  - Profit = {total_revenue} - {total_cost} = {profit}""",
        solutions=[str(profit)],
        level=13,
        problem_type="multi_step_word",
    )


def gen_distractor_word(rng: random.Random) -> MathTask:
    """Word problem with irrelevant numbers mixed in."""
    num_managers = rng.randint(3, 8)
    manager_salary = rng.randint(60, 120) * 1000
    num_workers = rng.randint(15, 40)
    widgets_per_day = rng.randint(20, 80)
    days_per_week = rng.choice([5, 6])
    weeks = rng.randint(2, 6)

    # Answer only involves workers * widgets * days * weeks
    total_days = days_per_week * weeks
    total_widgets = num_workers * widgets_per_day * total_days

    return MathTask(
        title="Word problem with distractors",
        spec=f"""TASK: A factory has {num_managers} managers (each earning ${manager_salary:,}
per year) and {num_workers} production workers. Each worker produces {widgets_per_day}
widgets per day. The factory operates {days_per_week} days per week.

How many widgets are produced in total over a {weeks}-week period?

OUTPUT FORMAT: A single integer.

VERIFICATION: Only workers and production days matter. Managers and salaries
are irrelevant to widget production count.""",
        solutions=[str(total_widgets)],
        level=13,
        problem_type="distractor_word",
    )


def gen_conditional_word(rng: random.Random) -> MathTask:
    """Word problem with conditional logic based on intermediate results."""
    base_value = rng.randint(50, 200)
    multiplier = rng.randint(3, 8)
    intermediate = base_value * multiplier
    threshold = rng.randint(100, 600)

    bonus_a = rng.randint(50, 200)
    penalty_b = rng.randint(20, 100)
    final_divisor = rng.choice([2, 3, 4, 5])

    if intermediate > threshold:
        after_condition = intermediate + bonus_a
        branch_desc = f"it exceeds {threshold}"
        action_desc = f"add a bonus of {bonus_a}"
    else:
        after_condition = intermediate - penalty_b
        branch_desc = f"it does not exceed {threshold}"
        action_desc = f"subtract a penalty of {penalty_b}"

    result = after_condition // final_divisor

    return MathTask(
        title="Conditional word problem",
        spec=f"""TASK: A process works as follows:

Step 1: Start with {base_value} and multiply by {multiplier}.
Step 2: Check the intermediate result:
  - If the result is GREATER than {threshold}, add a bonus of {bonus_a}.
  - If the result is {threshold} OR LESS, subtract a penalty of {penalty_b}.
Step 3: Divide the result by {final_divisor} using integer division.

What is the final result?

OUTPUT FORMAT: A single integer.

VERIFICATION:
  - Intermediate = {base_value} * {multiplier} = {intermediate}
  - Since {branch_desc}, {action_desc}: {after_condition}
  - Final = {after_condition} // {final_divisor} = {result}""",
        solutions=[str(result)],
        level=13,
        problem_type="conditional_word",
    )
