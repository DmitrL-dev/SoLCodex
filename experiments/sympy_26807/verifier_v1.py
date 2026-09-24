#!/usr/bin/env python3
"""Private development acceptance matrix for SymPy issue 26807.

Usage: sympy-venv/bin/python -I -B verify_sympy.py SOURCE_ROOT --json REPORT
Exit 0: every behavioral check passed. Exit 1: behavioral failure. Exit 2:
infrastructure/import failure. This is an oracle, not a security sandbox.
No expected values are read from a gold checkout or its tests.
"""
import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import signal
import sys
import time


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def build_suite(source_root):
    root = Path(source_root).resolve(strict=True)
    sys.path.insert(0, str(root))
    import numpy as np
    import sympy as sp
    from sympy.tensor.array.expressions import ArraySymbol
    from sympy.tensor.array.expressions.array_expressions import ArrayElement
    from sympy.utilities.iterables import iterable, is_sequence
    from sympy.utilities.lambdify import implemented_function

    require(Path(sp.__file__).resolve().is_relative_to(root),
            'SymPy was not imported from the requested source root')
    cases = []

    def case(name, category):
        def register(fn):
            cases.append((name, category, fn))
            return fn
        return register

    def equal_array(actual, expected):
        require(type(actual) is np.ndarray, 'result must be a NumPy ndarray')
        require(actual.shape == expected.shape, 'array shape changed')
        require(actual.dtype == expected.dtype, 'array dtype changed')
        require(np.array_equal(actual, expected), 'array values changed')

    def equal_scalar(actual, expected):
        require(isinstance(actual, (int, float, complex, np.number)),
                'result must be numeric, not a symbolic placeholder')
        require(np.ndim(actual) == 0 and actual == expected,
                f'scalar value mismatch: {actual!r} != {expected!r}')

    def call_unchanged(fn, *args):
        snapshots = [x.copy() if isinstance(x, np.ndarray) else None for x in args]
        answer = fn(*args)
        for arg, snapshot in zip(args, snapshots):
            if snapshot is not None:
                equal_array(arg, snapshot)
        return answer

    def raises_exact(kind, fn):
        try:
            fn()
        except Exception as exc:
            require(type(exc) is kind, f'expected {kind.__name__}, got {type(exc).__name__}')
        else:
            raise AssertionError(f'expected {kind.__name__}, nothing was raised')

    def vectors():
        return [np.array([2, -3, 7], dtype=np.int64),
                np.array([-11, 5, 0], dtype=np.int64),
                np.array([0.5, -1.25, 4.0], dtype=np.float64)]

    a = ArraySymbol('a', (3,))
    b = ArraySymbol('b', (3,))
    m = ArraySymbol('m', (2, 3))
    x, y, z = sp.symbols('x y z')

    @case('R01_issue_bare_argument_default_modules', 'regression')
    def _():
        fn = sp.lambdify(a, a)
        for value in vectors():
            equal_array(call_unchanged(fn, value), value)

    @case('R02_tuple_argument_integer_and_complex_identity', 'regression')
    def _():
        fn = sp.lambdify((a,), a, 'numpy')
        for value in vectors() + [np.array([2+3j, -4j, 0], dtype=np.complex128)]:
            equal_array(call_unchanged(fn, value), value)

    @case('R03_list_argument_rank_two_identity', 'regression')
    def _():
        fn = sp.lambdify([m], m, 'numpy')
        for value in [np.array([[2, -3, 7], [11, 0, -5]], dtype=np.int32),
                      np.arange(6, dtype=np.float64).reshape(2, 3) / 4]:
            equal_array(call_unchanged(fn, value), value)

    @case('R04_symbolic_extent_identity', 'regression')
    def _():
        n = sp.Symbol('n', positive=True, integer=True)
        arr = ArraySymbol('dynamic', (n,))
        fn = sp.lambdify(arr, arr, 'numpy')
        for size in [1, 4, 7]:
            value = np.arange(size, dtype=np.int64) * 3 - 5
            equal_array(call_unchanged(fn, value), value)

    @case('R05_cse_dummify_and_short_docstring', 'regression')
    def _():
        fn = sp.lambdify((a,), a, 'numpy', cse=True, dummify=True, docstring_limit=0)
        for value in vectors():
            equal_array(call_unchanged(fn, value), value)

    @case('R06_vector_element_polynomial', 'regression')
    def _():
        fn = sp.lambdify(a, 2*a[0] + a[1]**2 - a[2], 'numpy')
        for value in vectors():
            expected = 2*value[0] + value[1]**2 - value[2]
            equal_scalar(call_unchanged(fn, value), expected)

    @case('R07_matrix_element_polynomial', 'regression')
    def _():
        fn = sp.lambdify(m, m[0, 2] - 2*m[1, 0] + m[1, 1]**2, 'numpy')
        for value in [np.array([[2, -3, 7], [11, 0, -5]]),
                      np.array([[0.5, 2., -7.], [-1., 3.5, 8.]])]:
            expected = value[0, 2] - 2*value[1, 0] + value[1, 1]**2
            equal_scalar(call_unchanged(fn, value), expected)

    @case('R08_multiple_array_arguments', 'regression')
    def _():
        fn = sp.lambdify((a, b), a[0]*b[2] + a[2]*b[0] - b[1], 'numpy')
        for left, right in [(vectors()[0], np.array([-5, 11, 13])),
                            (vectors()[1], vectors()[2])]:
            expected = left[0]*right[2] + left[2]*right[0] - right[1]
            equal_scalar(call_unchanged(fn, left, right), expected)

    @case('R09_list_output_values_and_structure', 'regression')
    def _():
        fn = sp.lambdify(a, [a, a[1], a[0]**2 + a[2]], 'numpy')
        for value in vectors():
            actual = call_unchanged(fn, value)
            require(type(actual) is list and len(actual) == 3, 'list structure changed')
            equal_array(actual[0], value)
            equal_scalar(actual[1], value[1])
            equal_scalar(actual[2], value[0]**2 + value[2])

    @case('R10_dict_output_keys_values_and_structure', 'regression')
    def _():
        fn = sp.lambdify(a, {0: a, 1: a[1], 4: a[2] - a[0]}, 'numpy')
        for value in vectors():
            actual = call_unchanged(fn, value)
            require(type(actual) is dict and set(actual) == {0, 1, 4}, 'dict keys changed')
            equal_array(actual[0], value)
            equal_scalar(actual[1], value[1])
            equal_scalar(actual[4], value[2] - value[0])

    @case('R11_nested_tuple_output', 'regression')
    def _():
        fn = sp.lambdify(a, (a, (a[0], a[2])), 'numpy')
        for value in vectors():
            actual = call_unchanged(fn, value)
            require(type(actual) is tuple and len(actual) == 2, 'outer tuple changed')
            require(type(actual[1]) is tuple and len(actual[1]) == 2, 'inner tuple changed')
            equal_array(actual[0], value)
            equal_scalar(actual[1][0], value[0])
            equal_scalar(actual[1][1], value[2])

    @case('R12_array_element_implemented_function', 'regression')
    def _():
        bump = implemented_function('array_bump', lambda v: 3*v + 2)
        fn = sp.lambdify(a, bump(a[1]) + a[0], 'numpy')
        for value in vectors():
            equal_scalar(call_unchanged(fn, value), 3*value[1] + 2 + value[0])

    @case('R13_nonidentifier_name_with_dummify', 'regression')
    def _():
        arr = ArraySymbol('array value', (3,))
        fn = sp.lambdify((arr,), arr, 'numpy', dummify=True)
        for value in vectors():
            equal_array(call_unchanged(fn, value), value)

    @case('R14_math_backend_with_python_list', 'regression')
    def _():
        fn = sp.lambdify(a, 2*a[0] + a[1]**2 - a[2], 'math')
        for value in [[2, -3, 7], [-11, 5, 0], [0.5, -1.25, 4.0]]:
            before = value[:]
            equal_scalar(fn(value), 2*value[0] + value[1]**2 - value[2])
            require(value == before, 'Python input list changed')

    @case('R15_rank_three_identity', 'regression')
    def _():
        arr = ArraySymbol('volume', (2, 1, 3))
        fn = sp.lambdify(arr, arr, 'numpy')
        for value in [np.arange(6, dtype=np.int64).reshape(2, 1, 3),
                      np.arange(6, dtype=np.float64).reshape(2, 1, 3)*0.25 - 3.0]:
            equal_array(call_unchanged(fn, value), value)

    @case('R16_array_symbols_not_iterable', 'regression')
    def _():
        n = sp.Symbol('n', positive=True, integer=True)
        for shape in [(3,), (2, 3), (2, 1, 3), (n,)]:
            require(iterable(ArraySymbol('probe', shape)) is False, 'ArraySymbol treated as iterable')

    @case('R17_array_symbols_not_sequences', 'regression')
    def _():
        for shape in [(3,), (2, 3), (2, 1, 3)]:
            require(is_sequence(ArraySymbol('probe', shape)) is False, 'ArraySymbol treated as sequence')

    @case('C01_scalar_math_semantics', 'control')
    def _():
        fn = sp.lambdify(x, x**2 - 3*x + 2, 'math')
        for value in [-7, 0, 2, 3.5]:
            equal_scalar(fn(value), value**2 - 3*value + 2)

    @case('C02_numpy_vectorized_scalar_semantics', 'control')
    def _():
        fn = sp.lambdify(x, x**2 - 3*x + 2, 'numpy')
        for value in vectors():
            equal_array(call_unchanged(fn, value), value**2 - 3*value + 2)

    @case('C03_python_sequences_remain_iterable', 'control')
    def _():
        for value in [[2, -3, 7], (2, -3, 7), sp.Tuple(2, -3, 7)]:
            require(iterable(value) is True and is_sequence(value) is True, 'real sequence classification changed')
            require(list(value) == [2, -3, 7], 'real sequence values changed')

    @case('C04_concrete_sympy_array_remains_iterable', 'control')
    def _():
        value = sp.Array([2, -3, 7])
        require(iterable(value) is True and is_sequence(value) is True, 'concrete array classification changed')
        require(list(value) == [2, -3, 7], 'concrete array values changed')

    @case('C05_numpy_array_remains_iterable', 'control')
    def _():
        value = np.array([2, -3, 7])
        require(iterable(value) is True and is_sequence(value) is True, 'NumPy array classification changed')
        require(list(value) == [2, -3, 7], 'NumPy iteration changed')

    @case('C06_matrix_symbol_semantics', 'control')
    def _():
        matrix = sp.MatrixSymbol('matrix', 2, 2)
        fn = sp.lambdify(matrix, matrix*matrix + matrix.T, 'numpy')
        value = np.array([[2, -3], [7, 11]])
        equal_array(call_unchanged(fn, value), value @ value + value.T)

    @case('C07_array_element_ast_and_shape', 'control')
    def _():
        require(a.shape == sp.Tuple(3) and m.shape == sp.Tuple(2, 3), 'symbol shape changed')
        require(a[2] == ArrayElement(a, (2,)), 'vector indexing AST changed')
        element = m[1, 2]
        require(type(element) is ArrayElement and element.name == m and element.indices == sp.Tuple(1, 2),
                'matrix element AST changed')

    @case('C08_explicit_array_semantics', 'control')
    def _():
        actual = m.as_explicit()
        expected = sp.Array([[ArrayElement(m, (i, j)) for j in range(3)] for i in range(2)])
        require(actual == expected and actual.shape == (2, 3), 'explicit array changed')

    @case('C09_array_element_derivative', 'control')
    def _():
        require(sp.diff(a[0]**2 + 3*a[1], a[0]) == 2*a[0], 'element derivative changed')
        require(sp.diff(a[0]**2 + 3*a[1], a[1]) == 3, 'independent element derivative changed')

    @case('C10_symbolic_extent_explicit_conversion_rejected', 'negative_control')
    def _():
        n = sp.Symbol('n', positive=True, integer=True)
        raises_exact(ValueError, lambda: ArraySymbol('dynamic', (n,)).as_explicit())

    @case('C11_negative_indices_rejected', 'negative_control')
    def _():
        raises_exact(ValueError, lambda: a[-1])
        raises_exact(ValueError, lambda: m[0, -1])

    @case('C12_out_of_bounds_indices_rejected', 'negative_control')
    def _():
        raises_exact(ValueError, lambda: a[3])
        raises_exact(ValueError, lambda: m[2, 0])
        raises_exact(ValueError, lambda: m[0, 3])

    @case('C13_wrong_index_rank_rejected', 'negative_control')
    def _():
        raises_exact(IndexError, lambda: a[0, 1])
        raises_exact(IndexError, lambda: m[0])
        raises_exact(IndexError, lambda: m[()])

    @case('C14_scalar_implemented_function_semantics', 'control')
    def _():
        bump = implemented_function('scalar_bump', lambda v: 3*v + 2)
        fn = sp.lambdify(x, bump(x) + x**2, 'math')
        for value in [-7, 0, 2, 3.5]:
            equal_scalar(fn(value), 3*value + 2 + value**2)

    @case('C15_nested_ordinary_arguments_and_output', 'control')
    def _():
        fn = sp.lambdify(((x, y), z), [x + 2*y, z*x - y], 'math')
        for left, right, scalar in [(2, -3, 7), (-4, 6, -1)]:
            actual = fn((left, right), scalar)
            require(type(actual) is list and actual == [left + 2*right, scalar*left - right],
                    'nested argument unpacking or list output changed')

    @case('C16_scalars_strings_and_generators_classification', 'control')
    def _():
        for value in [x, 3, 'text', None]:
            require(iterable(value) is False and is_sequence(value) is False, 'scalar classification changed')
        generator = (i*i for i in range(3))
        require(iterable(generator) is True and is_sequence(generator) is False, 'generator classification changed')
        require(list(generator) == [0, 1, 4], 'classification consumed the generator')

    @case('C17_conflicting_implementations_rejected', 'negative_control')
    def _():
        first = implemented_function('conflict', lambda v: v + 1)
        second = implemented_function('conflict', lambda v: v + 2)
        raises_exact(ValueError, lambda: sp.lambdify(x, [first(x), second(x)], 'math'))

    @case('C18_implementation_namespace_override_semantics', 'control')
    def _():
        bump = implemented_function('override_bump', lambda v: v + 1)
        fn = sp.lambdify(x, bump(x), [{'override_bump': lambda v: 2*v - 3}, 'math'], use_imps=False)
        for value in [-7, 0, 4]:
            equal_scalar(fn(value), 2*value - 3)

    metadata = {
        'source_root': str(root), 'sympy_import_path': str(Path(sp.__file__).resolve()),
        'sympy_version': sp.__version__, 'python': sys.version, 'python_executable': sys.executable,
        'platform': platform.platform(), 'machine': platform.machine(),
        'recursion_limit': sys.getrecursionlimit(),
        'dependencies': {name: importlib.metadata.version(name)
                         for name in ['numpy', 'mpmath', 'pytest', 'hypothesis']},
        'verifier_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_key_sha256': hashlib.sha256((root / 'sympy/tensor/array/expressions/array_expressions.py').read_bytes()).hexdigest(),
    }
    return cases, metadata


class CheckTimeout(TimeoutError):
    pass


def run_suite(cases, metadata, seconds=10):
    results = []

    def timeout_handler(signum, frame):
        raise CheckTimeout('per-check deadline exceeded')

    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    try:
        for name, category, fn in cases:
            started = time.perf_counter()
            result = {'id': name, 'category': category}
            signal.setitimer(signal.ITIMER_REAL, seconds)
            try:
                fn()
            except Exception as exc:
                result.update(status='fail', error_type=type(exc).__name__, error=str(exc)[:500])
            else:
                result['status'] = 'pass'
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
            result['elapsed_seconds'] = round(time.perf_counter() - started, 6)
            results.append(result)
    finally:
        signal.signal(signal.SIGALRM, old_handler)
    passed = sum(r['status'] == 'pass' for r in results)
    return {
        'schema': 'sympy-26807-development-verifier-v1', 'metadata': metadata,
        'passed': passed, 'total': len(results), 'failed': len(results)-passed,
        'error_types': dict(Counter(r['error_type'] for r in results if r['status'] == 'fail')),
        'results': results,
        'limitations': ['Exposed development fixture; no held-out inference.',
                        'Behavioral oracle only; candidate code executes in this process.',
                        'Finite checks and illustrative mutants do not prove general correctness.'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_root')
    parser.add_argument('--json', type=Path)
    options = parser.parse_args()
    try:
        cases, metadata = build_suite(options.source_root)
        report = run_suite(cases, metadata)
        code = 0 if report['failed'] == 0 else 1
    except Exception as exc:
        report = {'status': 'infrastructure_error', 'error_type': type(exc).__name__, 'error': str(exc)}
        code = 2
    if options.json:
        options.json.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    if code == 2:
        print(json.dumps(report))
    else:
        for result in report['results']:
            print(result['status'].upper(), result['id'], result.get('error_type', ''))
        print(f"TOTAL {report['passed']}/{report['total']}; failures={report['failed']}")
    return code


if __name__ == '__main__':
    raise SystemExit(main())
