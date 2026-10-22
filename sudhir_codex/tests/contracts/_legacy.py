"""Run existing narrow regressions under stable fork-owned contract names."""

import importlib.util
import sys
import unittest
from collections.abc import Iterable
from functools import cache
from pathlib import Path


@cache
def legacy_module(name: str):
    """Load one legacy test module without exposing its TestCase classes here."""

    tests_root = Path(__file__).resolve().parents[1]
    module_path = tests_root / f"{name}.py"
    if not module_path.is_file():
        raise ImportError(f"legacy test module does not exist: {module_path}")
    tests_root_text = str(tests_root)
    if tests_root_text not in sys.path:
        sys.path.insert(0, tests_root_text)
    module_name = f"_sudhir_legacy_{name}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load legacy test module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def run_cases(
    owner: unittest.TestCase,
    cases: Iterable[tuple[type[unittest.TestCase], str]],
) -> None:
    """Run exact existing test methods and preserve their full failure output."""

    result = unittest.TestResult()
    for case_type, method_name in cases:
        case_type(methodName=method_name).run(result)
    problems = [
        *(f"FAIL {case.id()}\n{trace}" for case, trace in result.failures),
        *(f"ERROR {case.id()}\n{trace}" for case, trace in result.errors),
        *(f"UNEXPECTED SUCCESS {case.id()}" for case in result.unexpectedSuccesses),
    ]
    owner.assertFalse(problems, "\n\n".join(problems))
