"""Prospective development: transport consistency component; not a trusted runner.

Caller must use Lifecycle as a context manager around ALL relevant operations.
Stage receipts and after-exit evidence must be independently established outside
mutable repository execution. This module checks their exact consistency; hashes,
booleans, context managers and same-process state do not authenticate events.
No V5 integration, source loading, origin-schema interpretation or qualification.
"""
from __future__ import annotations

import hashlib
import json
import re


BINDINGS_SCHEMA = "solcodex.runtime-finalization-bindings.v1"
STAGE_SCHEMA = "solcodex.runtime-finalization-stage.v1"
ARTIFACT_SCHEMA = "solcodex.runtime-finalization-artifact.v1"
HOST_SCHEMA = "solcodex.runtime-finalization-after-exit.v1"
RESULT_SCHEMA = "solcodex.runtime-finalization-assessment.v1"
# Only this component's small stage/artifact/after-exit JSON envelopes use 2 MiB.
# Opaque complete payload budgets match the separately reviewed components;
# these constants are not caller-selectable caps or content validation.
MAX_RAW = 2 * 1024 * 1024
MAX_ORIGIN_BYTES = 8 * 1024 * 1024  # control_runtime_origins_v1.MAX_BYTES
MAX_REPORT_BYTES = 32 * 1024 * 1024  # Existing V5 collector budget, not V5 integration.
MAX_FAILURES = 16
STAGES = (
    "startup_verified", "runtime_entered", "pytest_unconfigured", "pytest_returned",
    "finalizing_entered", "origins_validated", "report_validated",
    "runtime_guard_passed", "cleanup_complete", "finalized",
)
HASH_BINDINGS = (
    "runtime_policy_sha256", "catalog_sha256", "source_manifest_sha256",
    "environment_sha256", "snapshot_sha256", "input_sha256",
    "launcher_source_sha256", "observer_source_sha256", "finalizer_source_sha256",
    "protected_startup_sha256",
)
PREREQUISITES = (
    "host_authentication_sha256", "origin_validation_sha256",
    "report_validation_sha256", "runtime_guard_sha256", "cleanup_sha256",
    "process_tree_termination_sha256", "host_cleanup_sha256", "output_custody_sha256",
)


class FinalizationError(ValueError):
    """Unresolved evidence; never a passing result or a score."""


def require(value, diagnostic):
    if not value:
        raise FinalizationError(diagnostic)


def fields(value, names, diagnostic):
    require(type(value) is dict and set(value) == set(names), diagnostic)


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
            "exact SHA256 required")
    return value


def integer(value):
    require(type(value) is int and 0 < value < 2**63, "positive sequence required")
    return value


def text(value):
    require(type(value) is str and 0 < len(value) <= 4096 and "\x00" not in value,
            "bounded text required")
    return value


def canonical(value):
    """Exact ASCII JSON plus LF. Not the digest domain of other components."""
    try:
        raw = (json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise FinalizationError("unrepresentable JSON") from error
    require(len(raw) <= MAX_RAW, "artifact byte limit")
    return raw


def raw_sha256(raw):
    """Exact envelope bytes, at most MAX_RAW; not an opaque payload helper."""
    require(type(raw) is bytes and 0 < len(raw) <= MAX_RAW, "bounded raw bytes required")
    return hashlib.sha256(raw).hexdigest()


def origin_sha256(raw):
    """Hash complete opaque origin bytes within the reviewed 8 MiB budget.

    No decode, truncation, caller cap, schema validation or event authentication.
    Separate origin validation must cover these identical retained bytes.
    """
    require(type(raw) is bytes and 0 < len(raw) <= MAX_ORIGIN_BYTES,
            "origin payload bytes exceed reviewed budget or have invalid type")
    return hashlib.sha256(raw).hexdigest()


def report_sha256(raw):
    """Hash complete opaque report bytes within the reviewed 32 MiB budget.

    Budget compatibility alone does not define a prospective V6 report schema
    or reinterpret V5 process status. Separate report validation remains required.
    """
    require(type(raw) is bytes and 0 < len(raw) <= MAX_REPORT_BYTES,
            "report payload bytes exceed reviewed budget or have invalid type")
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise FinalizationError("nonfinite JSON number")


def decode(raw):
    raw_sha256(raw)
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise FinalizationError("invalid JSON") from error
    require(canonical(value) == raw, "canonical ASCII JSON plus LF required")
    return value


def bindings(value):
    fields(value, ("schema", "job_id", "nonce", "arm", "ordinal", *HASH_BINDINGS),
           "exact bindings required")
    require(type(value["schema"]) is str and value["schema"] == BINDINGS_SCHEMA, "bindings schema differs")
    text(value["job_id"])
    sha(value["nonce"])
    require(type(value["arm"]) is str and value["arm"] in ("base", "gold"), "control arm required")
    require(type(value["ordinal"]) is int and value["ordinal"] in (0, 1, 2),
            "first-three ordinal required")
    for key in HASH_BINDINGS:
        sha(value[key])
    return decode(canonical(value))


def _stage(value, expected, index, previous, refs, seen_evidence):
    fields(value, ("schema", "bindings", "stage", "sequence", "evidence_sha256",
                   "pytest_status", "report_sha256", "origin_sha256"),
           "exact stage fields required")
    require(value["schema"] == STAGE_SCHEMA and bindings(value["bindings"]) == expected,
            "stage schema/bindings differ")
    require(index < len(STAGES) and value["stage"] == STAGES[index],
            "stage order or multiplicity differs")
    require(integer(value["sequence"]) > previous, "stage sequence not increasing")
    evidence = sha(value["evidence_sha256"])
    require(evidence not in seen_evidence, "stage evidence reused")
    if index < 3:
        require(value["pytest_status"] is None, "pytest return not yet witnessed")
    else:
        require(type(value["pytest_status"]) is int
                and value["pytest_status"] == (1 if expected["arm"] == "base" else 0),
                "pytest status differs from arm")
    if index < 4:
        require(value["report_sha256"] is None and value["origin_sha256"] is None,
                "terminal artifact references precede finalizing")
    else:
        pair = (sha(value["report_sha256"]), sha(value["origin_sha256"]))
        require(refs is None or pair == refs, "report/origin references changed")
        refs = pair
    return refs


def _exception_types(error):
    """Bounded native cause/context type diagnostics; NOT full exception evidence.

    Do not call error.__str__/repr. Original exceptions propagate unchanged.
    Full messages/tracebacks/groups and actual event authentication belong to
    independently retained failure/origin evidence, not these diagnostics.
    """
    pending, nodes, ids = [error], [], {id(error): 0}
    for current in pending:
        require(len(pending) <= 16, "exception diagnostic limit; retain original exception")
        cls = type(current)
        module = type.__getattribute__(cls, "__module__")
        name = type.__getattribute__(cls, "__qualname__")
        text(module)
        text(name)
        node = {"type": module + "." + name}
        for edge in ("__cause__", "__context__"):
            child = BaseException.__dict__[edge].__get__(current)
            if child is not None and id(child) not in ids:
                ids[id(child)] = len(pending)
                pending.append(child)
            node[edge] = None if child is None else ids[id(child)]
        node["suppressed"] = BaseException.__suppress_context__.__get__(current)
        nodes.append(node)
    return nodes


class Lifecycle:
    """Single-use STARTUP/RUNTIME/FINALIZING/FINALIZED or permanent FAILED.

    State is orchestration, not isolation. Receipts record actual unconfigure
    BEFORE pytest.main return. Cleanup here is runtime cleanup; host process-tree
    termination and post-exit cleanup are separate mandatory parent evidence.
    """
    def __init__(self, expected_bindings):
        self._bindings = bindings(expected_bindings)
        self._state = "STARTUP"
        self._stages = []
        self._failures = []
        self._refs = None
        self._entered = False
        self._closed = False
        self._diagnostic_incomplete = False

    @property
    def state(self):
        return self._state

    def _failed(self, stage, code, error=None, evidence_sha256=None):
        self._state = "FAILED"  # Poison before any potentially failing diagnostic work.
        if len(self._failures) >= MAX_FAILURES:
            self._diagnostic_incomplete = True
            raise FinalizationError("failure record limit; external evidence required")
        record = {"stage": stage, "code": code, "evidence_sha256": evidence_sha256,
                  "exception_types": None}
        self._failures.append(record)
        if error is not None:
            try:
                record["exception_types"] = _exception_types(error)
            except BaseException:
                self._diagnostic_incomplete = True
                raise  # Preserve diagnostic failure and its original exception context.

    def _next(self):
        return STAGES[len(self._stages)] if len(self._stages) < len(STAGES) else "after_finalized"

    def __enter__(self):
        if self._entered or self._closed or self._state != "STARTUP":
            self._failed(self._next(), "context_reentry")
            raise FinalizationError("lifecycle cannot be reentered")
        self._entered = True
        return self

    def advance(self, receipt):
        try:
            require(self._entered and not self._closed and self._state != "FAILED",
                    "inactive or failed lifecycle")
            copied = decode(canonical(receipt))
            index = len(self._stages)
            refs = _stage(copied, self._bindings, index,
                          self._stages[-1]["sequence"] if self._stages else 0,
                          self._refs, {r["evidence_sha256"] for r in self._stages})
            self._stages.append(copied)
            self._refs = refs
            if index == 1:
                self._state = "RUNTIME"
            elif index == 4:
                self._state = "FINALIZING"
            elif index == 9:
                self._state = "FINALIZED"
        except BaseException as error:
            self._failed(self._next(), "stage_rejected", error)
            raise

    def abort(self, stage, code, *, evidence_sha256):
        """Record independently retained failure causes by opaque evidence ref.

        The caller must abort on caught startup/runtime/guard/cleanup failures.
        Uncaught BaseExceptions are also recorded by context exit, never suppressed.
        """
        try:
            require(stage in STAGES or stage == "after_finalized", "unknown failure stage")
            text(code)
            sha(evidence_sha256)
        except BaseException as error:
            self._failed(self._next(), "invalid_failure_record", error)
            raise
        self._failed(stage, code, evidence_sha256=evidence_sha256)
        raise FinalizationError("explicit lifecycle failure: " + code)

    def __exit__(self, kind, error, traceback):
        self._closed = True
        if error is not None:
            self._failed(self._next(), "terminal_exception", error)
            return False
        if self._state != "FINALIZED" or len(self._stages) != len(STAGES):
            if self._state != "FAILED":
                self._failed(self._next(), "incomplete_lifecycle")
            raise FinalizationError("lifecycle did not finalize")
        return False

    def artifact_bytes(self):
        if not self._closed:
            self._failed(self._next(), "premature_terminal_artifact")
            raise FinalizationError("no terminal artifact before context exit")
        require(self._state in ("FINALIZED", "FAILED"), "terminal state required")
        require(not self._diagnostic_incomplete, "failure diagnostics incomplete; retain external evidence")
        return canonical({
            "schema": ARTIFACT_SCHEMA, "bindings": self._bindings, "state": self._state,
            "stages": self._stages, "failures": self._failures,
        })


def validate_terminal(raw, *, expected_bindings, report_raw, origin_raw, after_exit):
    """Parent-side exact consistency check AFTER independently retained exit.

    after_exit and expected_bindings MUST come from independently authenticated
    host evidence, not the producer or a copy of its receipts. Separate origin
    and report validators must have succeeded on these exact opaque byte strings.
    This function does not parse their schemas or authenticate host/event truth.
    Envelope, origin and report bounds are separately fixed at 2, 8 and 32 MiB.
    """
    expected = bindings(expected_bindings)
    artifact = decode(raw)
    host = decode(canonical(after_exit))
    fields(artifact, ("schema", "bindings", "state", "stages", "failures"), "artifact fields differ")
    fields(host, ("schema", "bindings", "artifact_sha256", "report_sha256", "origin_sha256",
                  "stages", "failures", "process_exitstatus", "pytest_status",
                  "process_exit_sequence", "termination_sequence", "host_cleanup_sequence",
                  "receipt_sequence", "prerequisites"), "after-exit fields differ")
    require(artifact["schema"] == ARTIFACT_SCHEMA and host["schema"] == HOST_SCHEMA,
            "prospective terminal schema required")
    require(bindings(artifact["bindings"]) == expected and bindings(host["bindings"]) == expected,
            "terminal bindings differ")
    require(artifact["state"] == "FINALIZED" and artifact["failures"] == []
            and host["failures"] == [], "failed or uncertain terminal evidence")
    require(type(host["process_exitstatus"]) is int and host["process_exitstatus"] == 0,
            "transport exit must be zero")
    require(type(host["pytest_status"]) is int
            and host["pytest_status"] == (1 if expected["arm"] == "base" else 0),
            "host pytest status differs from arm")
    require(sha(host["artifact_sha256"]) == raw_sha256(raw)
            and sha(host["report_sha256"]) == report_sha256(report_raw)
            and sha(host["origin_sha256"]) == origin_sha256(origin_raw),
            "independent raw artifact/report/origin hash differs")
    stages = artifact["stages"]
    require(type(stages) is list and type(host["stages"]) is list
            and len(stages) == len(STAGES) and canonical(stages) == canonical(host["stages"]),
            "independent complete stage sequence differs")
    refs, previous, seen = None, 0, set()
    for index, receipt in enumerate(stages):
        refs = _stage(receipt, expected, index, previous, refs, seen)
        previous = receipt["sequence"]
        seen.add(receipt["evidence_sha256"])
    require(refs == (host["report_sha256"], host["origin_sha256"]), "terminal raw references differ")
    for key in ("process_exit_sequence", "termination_sequence", "host_cleanup_sequence", "receipt_sequence"):
        require(integer(host[key]) > previous, "after-exit/termination/cleanup order differs")
        previous = host[key]
    pins = host["prerequisites"]
    fields(pins, PREREQUISITES, "independent prerequisite references required")
    for value in pins.values():
        sha(value)
    for key, index in (("origin_validation_sha256", 5), ("report_validation_sha256", 6),
                       ("runtime_guard_sha256", 7), ("cleanup_sha256", 8)):
        require(pins[key] == stages[index]["evidence_sha256"], "independent prerequisite/stage differs")
    return {"schema": RESULT_SCHEMA, "status": "transport-consistent-only",
            "artifact_sha256": raw_sha256(raw), "pytest_status": host["pytest_status"],
            "candidate_acceptance_authorized": False, "host_authentication_established": False}
