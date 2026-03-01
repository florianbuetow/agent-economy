"""CLI entry point for math task factory."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _find_project_root() -> Path:
    """Find project root by walking up from cwd until we find tools/ or services/."""
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / "tools").is_dir() or (parent / "services").is_dir():
            return parent
    return cwd


def _task_to_dict(task: object) -> dict:
    from math_task_factory import MathTask

    t = task
    if not isinstance(t, MathTask):
        raise TypeError(f"Expected MathTask, got {type(t)}")
    d: dict = {
        "title": t.title,
        "spec": t.spec,
        "solutions": t.solutions,
        "level": t.level,
        "problem_type": t.problem_type,
    }
    if t.solution_note is not None:
        d["solution_note"] = t.solution_note
    return d


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate mathematical problem tasks for agent economy testing."
    )
    parser.add_argument(
        "--level",
        type=int,
        metavar="N",
        help="Generate tasks at level N (1-9).",
    )
    parser.add_argument(
        "--levels",
        type=str,
        metavar="N-M",
        help="Generate tasks at levels N through M (e.g. 1-6).",
    )
    parser.add_argument(
        "--total",
        type=int,
        metavar="N",
        help="Generate exactly N tasks total, distributed across all levels (1-9).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        metavar="N",
        help="Number of tasks per level (default: 1). Ignored when --total is used.",
    )
    parser.add_argument(
        "--problem-type",
        type=str,
        metavar="TYPE",
        help="Specific problem type (e.g. addition_positive, system_solvable).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        metavar="N",
        help="Random seed for reproducibility.",
    )
    parser.add_argument(
        "--output",
        type=str,
        metavar="FILE",
        help="Write JSONL output to FILE. Default: data/math_tasks.jsonl in project root.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "pretty"],
        default="json",
        help="Output format (default: json).",
    )
    args = parser.parse_args()

    if args.total is not None:
        if args.total < 1:
            parser.error("--total must be at least 1")
        if args.level is not None or args.levels is not None:
            parser.error("--total cannot be combined with --level or --levels")
    else:
        if args.level is None and args.levels is None:
            parser.error("Provide --level, --levels, or --total")
        if args.level is not None and args.levels is not None:
            parser.error("Provide --level or --levels, not both")

    from math_task_factory import MathTaskFactory

    factory = MathTaskFactory(seed=args.seed)
    problem_type = args.problem_type if args.problem_type else None

    if args.total is not None:
        levels_tuple = tuple(range(1, 10))
        tasks = []
        for _ in range(args.total):
            level = factory._rng.choice(levels_tuple)
            tasks.append(factory.create(level=level, problem_type=problem_type))
    else:
        if args.level is not None:
            levels_tuple = (args.level,)
        else:
            parts = args.levels.split("-")
            if len(parts) != 2:
                parser.error("--levels must be N-M (e.g. 1-6)")
            try:
                lo, hi = int(parts[0]), int(parts[1])
            except ValueError:
                parser.error("--levels N-M: N and M must be integers")
            if lo > hi:
                parser.error("--levels: first number must be <= second")
            levels_tuple = tuple(range(lo, hi + 1))

        try:
            tasks = factory.create_batch(
                levels=levels_tuple,
                count=args.count,
                problem_type=problem_type,
            )
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    task_dicts = [_task_to_dict(t) for t in tasks]

    if args.output is not None:
        out_path = Path(args.output)
    else:
        project_root = _find_project_root()
        out_path = project_root / "data" / "math_tasks.jsonl"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for d in task_dicts:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"Wrote {len(task_dicts)} task(s) to {out_path}", file=sys.stderr)

    if args.format == "pretty":
        text = json.dumps({"tasks": task_dicts}, indent=2, ensure_ascii=False)
    else:
        text = json.dumps({"tasks": task_dicts}, ensure_ascii=False)
    print(text)

    return 0


if __name__ == "__main__":
    sys.exit(main())
