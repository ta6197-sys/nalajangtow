"""Stage 3: optional SALib adapters (no dependency until called).

IMPORTANT: Morris/Sobol experiments are not ordinary Monte Carlo draws.
The default Sobol design assumes independent uniform continuous variables.
These functions have an *injected* deterministic model evaluator; hook it to
validated scenario replay rather than changing contract/CP-SAT calculations.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import numpy as np


def _problem(bounds: Mapping[str, tuple[float, float]]) -> dict:
    if not bounds:
        raise ValueError("Provide at least one sensitivity variable")
    for name, pair in bounds.items():
        if not isinstance(name, str) or len(pair) != 2 or not (np.isfinite(pair[0]) and np.isfinite(pair[1])) or pair[0] >= pair[1]:
            raise ValueError(f"Invalid variable bounds: {name}")
    return {"num_vars": len(bounds), "names": list(bounds), "bounds": [list(x) for x in bounds.values()]}


def _evaluate(x: np.ndarray, names: list[str], evaluator: Callable[[dict], int | float]) -> np.ndarray:
    values = []
    for row in x:
        result = evaluator(dict(zip(names, map(float, row))))
        if isinstance(result, bool) or not np.isfinite(result):
            raise ValueError("Model evaluation must be a finite numeric scalar")
        values.append(float(result))
    return np.asarray(values, dtype=float)


def run_morris(bounds: Mapping[str, tuple[float, float]], evaluator: Callable[[dict], int | float], *,
               trajectories: int = 16, seed: int = 42) -> dict:
    """Run small screening design; does not imply real-world input probabilities."""
    try:
        from SALib.sample import morris as morris_sampler
        from SALib.analyze import morris as morris_analyzer
    except ImportError as exc:
        raise RuntimeError("Optional SALib not installed; install requirements-risk-sensitivity.txt") from exc
    problem = _problem(bounds)
    if trajectories < 2:
        raise ValueError("At least two trajectories required")
    x = morris_sampler.sample(problem, N=trajectories, num_levels=4, seed=seed)
    y = _evaluate(x, problem["names"], evaluator)
    metrics = morris_analyzer.analyze(problem, x, y, num_levels=4, print_to_console=False)
    return {"method": "MORRIS", "sample_evaluations": int(len(y)), "names": problem["names"],
            "mu_star": np.asarray(metrics["mu_star"]).tolist(),
            "sigma": np.asarray(metrics["sigma"]).tolist(),
            "input_assumption": "USER_SPECIFIED_BOUNDS"}


def run_sobol(bounds: Mapping[str, tuple[float, float]], evaluator: Callable[[dict], int | float], *,
              base_samples: int = 512, seed: int = 42) -> dict:
    """First and total Sobol indices, independent uniform inputs only."""
    try:
        from SALib.sample import sobol as sobol_sampler
        from SALib.analyze import sobol as sobol_analyzer
    except ImportError as exc:
        raise RuntimeError("Optional SALib not installed; install requirements-risk-sensitivity.txt") from exc
    problem = _problem(bounds)
    if base_samples < 2 or (base_samples & (base_samples - 1)):
        raise ValueError("Sobol base_samples must be a power of two >= 2")
    x = sobol_sampler.sample(problem, N=base_samples, calc_second_order=False, seed=seed)
    y = _evaluate(x, problem["names"], evaluator)
    metrics = sobol_analyzer.analyze(problem, y, calc_second_order=False, print_to_console=False)
    return {"method": "SOBOL", "sample_evaluations": int(len(y)), "names": problem["names"],
            "S1": np.asarray(metrics["S1"]).tolist(),
            "ST": np.asarray(metrics["ST"]).tolist(),
            "input_assumption": "INDEPENDENT_UNIFORM_WITHIN_BOUNDS"}
