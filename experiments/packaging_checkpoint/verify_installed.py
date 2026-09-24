"""Offline installed-wheel observations; expected values belong to the host.

Each case uses a fresh isolated Python process inside the caller's network-free
container. This is not a Python sandbox: hostile code sharing the interpreter can
still forge valid observations. Fake PASS output and early exit are rejected.
Upstream pytest/JUnit remains diagnostic, not a trusted adversarial verdict.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import secrets
import selectors
import signal
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

SITE = Path(os.environ.get("SOL_PACKAGING_SCRATCH", "/tmp")) / "candidate-site"
TRUSTED_DEPS = (
    "pytest-8.4.2-py3-none-any.whl", "iniconfig-2.1.0-py3-none-any.whl",
    "pluggy-1.6.0-py3-none-any.whl", "pygments-2.19.2-py3-none-any.whl",
)
# Inputs only. No expected values or pass/fail decisions in the candidate runner.
CASES = (
    ("nested-single", "canonicalize", "((MIT))"),
    ("nested-whitespace", "canonicalize", "(( MIT ))"),
    ("nested-and-or", "canonicalize", "((MIT AND (Apache-2.0 OR BSD-2-Clause)))"),
    ("nested-with", "canonicalize", "((GPL-2.0-only WITH Classpath-exception-2.0))"),
    ("nested-ref", "canonicalize", "((LicenseRef-Custom))"),
    ("metadata-nested", "metadata", "((MIT))"),
    ("simple", "canonicalize", "MIT"),
    ("single-parens", "canonicalize", "(MIT)"),
    ("invalid-1", "canonicalize", "()"),
    ("invalid-2", "canonicalize", "MIT Apache-2.0"),
    ("invalid-3", "canonicalize", "(MIT"),
    ("invalid-4", "canonicalize", "MIT OR"),
    ("invalid-5", "canonicalize", "Unknown-License"),
    ("invalid-6", "canonicalize", "MIT (Apache-2.0)"),
)
STDOUT_LIMIT = 16 * 1024
STDERR_LIMIT = 64 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def bounded_process(argv: list[str], payload: bytes = b"", timeout: float = 15,
                    stdout_limit: int = STDOUT_LIMIT) -> dict:
    """Drain both pipes with hard byte/deadline bounds; kill the process group."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTHON", "PYTEST", "PIP_"))}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
               PIP_CONFIG_FILE=os.devnull)
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    error = None
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True, env=env)
    deadline = time.monotonic() + timeout
    try:
        # All requests are fixed, small inputs (< PIPE_BUF), written before imports.
        if len(payload) > 4096:
            raise ValueError("request too large")
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except BrokenPipeError:
            process.stdin.close()
        with selectors.DefaultSelector() as selector:
            for name in outputs:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map() or process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    error = "timeout"
                    break
                for key, _ in selector.select(min(remaining, 0.1)):
                    name = key.data
                    limit = stdout_limit if name == "stdout" else STDERR_LIMIT
                    chunk = os.read(key.fd, min(65536, limit - len(outputs[name]) + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    outputs[name].extend(chunk)
                    if len(outputs[name]) > limit:
                        error = name + "_limit"
                        break
                if error:
                    break
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    return {"exit": process.returncode, "stdout": bytes(outputs["stdout"]),
            "stderr": bytes(outputs["stderr"]), "error": error}


def parse_observation(output: bytes, exit_code: int) -> dict | None:
    if (exit_code != 0 or len(output) > STDOUT_LIMIT or
            not output.endswith(b"\n") or output.count(b"\n") != 1):
        return None
    try:
        value = json.loads(output.decode("utf-8"), object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if type(kind) is not str:
        return None
    if kind == "value":
        valid = set(value) == {"kind", "type", "value"} and value['type'] == 'str' and type(value['value']) is str
    elif kind in {"exception", "unsupported_value"}:
        valid = set(value) == {"kind", "type"} and type(value['type']) is str and 0 < len(value['type']) <= 128
    elif kind == "import_error":
        valid = (set(value) == {"kind", "type", "module"} and
                 type(value['type']) is str and 0 < len(value['type']) <= 128 and
                 (value['module'] is None or type(value['module']) is str and len(value['module']) <= 512))
    else:
        valid = False
    return value if valid else None


def _imports():
    sys.path.insert(0, str(SITE))
    modules = [importlib.import_module(name) for name in
               ("packaging", "packaging.licenses", "packaging.metadata")]
    expected = (SITE / 'packaging/__init__.py', SITE / 'packaging/licenses/__init__.py',
                SITE / 'packaging/metadata.py')
    if any(Path(module.__file__).resolve() != path.resolve()
           for module, path in zip(modules, expected)):
        raise RuntimeError("installed import provenance differs")
    return modules[1:]


def case_child() -> int:
    raw = sys.stdin.buffer.read(4097)
    request = json.loads(raw, object_pairs_hook=_unique)
    if (len(raw) > 4096 or not isinstance(request, dict) or
            set(request) != {'op', 'expression'} or
            request['op'] not in {'canonicalize', 'metadata'} or
            type(request['expression']) is not str):
        raise ValueError("invalid case request")
    # Ordinary candidate prints go to diagnostics; early os._exit leaves no frame.
    result_fd = os.dup(1)
    os.dup2(2, 1)
    try:
        try:
            licenses, metadata = _imports()
        except (ImportError, RuntimeError) as error:
            observation = {'kind': 'import_error', 'type': type(error).__name__,
                           'module': getattr(error, 'name', None)}
        else:
            try:
                if request['op'] == 'canonicalize':
                    value = licenses.canonicalize_license_expression(request['expression'])
                else:
                    value = metadata.Metadata.from_raw(
                        {'license_expression': request['expression']}, validate=False).license_expression
                observation = ({'kind': 'value', 'type': 'str', 'value': str(value)}
                               if isinstance(value, str) else
                               {'kind': 'unsupported_value', 'type': type(value).__name__})
            except Exception as error:
                # Only genuine instances of the candidate API class get this label.
                label = ('InvalidLicenseExpression' if isinstance(error, licenses.InvalidLicenseExpression)
                         else 'other:' + type(error).__name__)
                observation = {'kind': 'exception', 'type': label}
        encoded = (json.dumps(observation, sort_keys=True, ensure_ascii=True) + '\n').encode()
        if len(encoded) > STDOUT_LIMIT:
            return 5
        with os.fdopen(result_fd, 'wb', closefd=False) as stream:
            stream.write(encoded)
        return 0
    finally:
        os.close(result_fd)


def upstream_child(trusted_tests: Path, junit: Path) -> int:
    _imports()
    os.environ.pop('PYTEST_ADDOPTS', None)
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    import pytest
    return int(pytest.main(['-q', '-p', 'no:cacheprovider', '-o', 'addopts=',
                           '--junitxml=' + str(junit), str(trusted_tests / 'test_metadata.py')]))


def parse_junit(path: Path) -> dict | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * 1024 * 1024:
                return None
            raw = stream.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return None
        root = ET.fromstring(raw)
        suites = [root] if root.tag == 'testsuite' else list(root) if root.tag == 'testsuites' else []
        if not suites or any(suite.tag != 'testsuite' for suite in suites):
            return None
        counts = {key: 0 for key in ('tests', 'failures', 'errors', 'skipped')}
        inventory = []
        for suite in suites:
            local = {key: int(suite.attrib[key]) for key in counts}
            cases = suite.findall('testcase')
            if (any(v < 0 for v in local.values()) or len(cases) != local['tests'] or
                    sum(local[k] for k in ('failures', 'errors', 'skipped')) > local['tests']):
                return None
            measured = {key: 0 for key in ('failures', 'errors', 'skipped')}
            for case in cases:
                identity = (case.attrib['classname'], case.attrib['name'])
                statuses = [tag for tag in ('failure', 'error', 'skipped') if case.find(tag) is not None]
                if len(statuses) > 1:
                    return None
                status = statuses[0] if statuses else 'passed'
                if statuses:
                    measured[{'failure': 'failures', 'error': 'errors', 'skipped': 'skipped'}[status]] += 1
                inventory.append({'classname': identity[0], 'name': identity[1], 'status': status})
            if any(measured[k] != local[k] for k in measured):
                return None
            for key in counts:
                counts[key] += local[key]
        if counts['tests'] < 1 or len({(r['classname'], r['name']) for r in inventory}) != len(inventory):
            return None
        return {**counts, 'inventory': sorted(inventory, key=lambda r: (r['classname'], r['name']))}
    except (OSError, ValueError, ET.ParseError, KeyError):
        return None


def run(wheel: Path, deps: Path, verifier: Path | None = None,
        trusted_tests: Path | None = None, *, phase: str = 'observations') -> dict:
    """verifier is retained for caller compatibility, but is never opened/executed."""
    if phase not in {'observations', 'upstream'}:
        raise ValueError('invalid evaluation phase')
    if phase == 'upstream' and trusted_tests is None:
        raise ValueError('trusted_tests required for upstream phase')
    report = {'schema': 'solcodex.packaging-installed-observations.v2',
              'phase': phase,
              'install_ok': False, 'import_ok': False, 'observations_complete': False,
              'observations': {}, 'import_error_type': None, 'import_error_module': None,
              'upstream_exit': None, 'upstream_summary': None,
              'failure_stage': None, 'failure_case': None, 'process_error': None}
    try:
        wheels = [wheel] + [deps / name for name in TRUSTED_DEPS]
        installed = bounded_process([sys.executable, '-m', 'pip', 'install', '--no-index',
                    '--no-deps', '--no-compile', '--disable-pip-version-check', '--target',
                    str(SITE), *map(str, wheels)], timeout=180, stdout_limit=STDERR_LIMIT)
        if installed['error'] or installed['exit'] != 0:
            report.update(failure_stage='wheel_install', process_error=installed['error'])
            return report
        report['install_ok'] = True
        base = [sys.executable, '-I', '-B', str(Path(__file__).resolve())]
        if phase == 'observations':
            for case_id, operation, expression in CASES:
                result = bounded_process(base + ['--child', 'case'],
                                         json.dumps({'op': operation, 'expression': expression}).encode())
                observation = parse_observation(result['stdout'], result['exit']) if not result['error'] else None
                if observation is None:
                    report.update(failure_stage='case_wire', failure_case=case_id, process_error=result['error'])
                    return report
                report['observations'][case_id] = observation
                if observation['kind'] == 'import_error':
                    report.update(failure_stage='case_import', failure_case=case_id,
                                  import_error_type=observation['type'], import_error_module=observation['module'])
                    return report
            report.update(import_ok=True, observations_complete=True)
            return report
        junit = SITE.parent / ('upstream-' + secrets.token_hex(16) + '.xml')
        try:
            upstream = bounded_process(base + ['--child', 'upstream', '--trusted-tests',
                       str(trusted_tests), '--junit', str(junit)], timeout=180, stdout_limit=1024 * 1024)
            report['upstream_exit'] = upstream['exit']
            report['upstream_summary'] = parse_junit(junit) if not upstream['error'] else None
            if report['upstream_summary'] is None:
                report.update(failure_stage='upstream_wire', process_error=upstream['error'])
            elif upstream['exit'] in (0, 1):
                report['import_ok'] = True
        finally:
            junit.unlink(missing_ok=True)
        return report
    except OSError:
        report.update(failure_stage='process_runtime', process_error='OSError')
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path)
    parser.add_argument('--deps', type=Path)
    parser.add_argument('--verifier', type=Path)  # compatibility only; not passed to children
    parser.add_argument('--trusted-tests', type=Path)
    parser.add_argument('--phase', choices=('observations', 'upstream'))
    parser.add_argument('--child', choices=('case', 'upstream'))
    parser.add_argument('--junit', type=Path)
    args = parser.parse_args()
    if args.child == 'case':
        return case_child()
    if args.child == 'upstream':
        if args.trusted_tests is None or args.junit is None:
            parser.error('trusted-tests and junit required')
        return upstream_child(args.trusted_tests, args.junit)
    if args.wheel is None or args.deps is None or args.phase is None:
        parser.error('wheel, deps and phase required')
    if args.phase == 'upstream' and args.trusted_tests is None:
        parser.error('trusted-tests required for upstream phase')
    print(json.dumps(run(args.wheel, args.deps, args.verifier, args.trusted_tests,
                         phase=args.phase), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
