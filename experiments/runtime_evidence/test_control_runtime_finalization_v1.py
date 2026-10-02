"""Synthetic development scenarios; no native/host attestation.

Fixtures below deliberately manufacture SYNTHETIC host/stage receipts to exercise
consistency rules. They must NEVER be used to authenticate actual observer bytes.
The cross-component scenario calls the actual origin artifact validator with a
synthetic full reference; it authenticates no native events. No compiler,
observer or candidate repository is imported. Runtime results belong to exact external verification receipts.
"""
import copy
import hashlib
import unittest

import control_runtime_origins_v1 as origins

from control_runtime_finalization_v1 import (
    ARTIFACT_SCHEMA, BINDINGS_SCHEMA, HASH_BINDINGS, HOST_SCHEMA, PREREQUISITES,
    STAGE_SCHEMA, STAGES, FinalizationError, Lifecycle, canonical, decode,
    MAX_RAW, MAX_ORIGIN_BYTES, MAX_REPORT_BYTES,
    raw_sha256, origin_sha256, report_sha256, validate_terminal,
)


def digest(label):
    return hashlib.sha256(("SYNTHETIC:" + label).encode()).hexdigest()


def expected(arm="base"):
    return {"schema": BINDINGS_SCHEMA, "job_id": "SYNTHETIC-job", "nonce": digest("nonce"),
            "arm": arm, "ordinal": 0, **{key: digest(key) for key in HASH_BINDINGS}}


def receipts(pins, report=b"SYNTHETIC report", origin=b"SYNTHETIC opaque origin"):
    return [{
        "schema": STAGE_SCHEMA, "bindings": copy.deepcopy(pins), "stage": stage,
        "sequence": (index + 1) * 10, "evidence_sha256": digest(stage),
        "pytest_status": None if index < 3 else (1 if pins["arm"] == "base" else 0),
        "report_sha256": None if index < 4 else report_sha256(report),
        "origin_sha256": None if index < 4 else origin_sha256(origin),
    } for index, stage in enumerate(STAGES)]


def host_fixture(pins, raw, stages, report, origin):
    # SYNTHETIC copying here is intentional for unit fixtures only. A real parent
    # must independently retain/verify receipts, not copy the producer's stream.
    prerequisites = {key: digest(key) for key in PREREQUISITES}
    for key, index in (("origin_validation_sha256", 5), ("report_validation_sha256", 6),
                       ("runtime_guard_sha256", 7), ("cleanup_sha256", 8)):
        prerequisites[key] = stages[index]["evidence_sha256"]
    return {
        "schema": HOST_SCHEMA, "bindings": copy.deepcopy(pins),
        "artifact_sha256": raw_sha256(raw), "report_sha256": report_sha256(report),
        "origin_sha256": origin_sha256(origin), "stages": copy.deepcopy(stages), "failures": [],
        "process_exitstatus": 0, "pytest_status": 1 if pins["arm"] == "base" else 0,
        "process_exit_sequence": 110, "termination_sequence": 120,
        "host_cleanup_sequence": 130, "receipt_sequence": 140, "prerequisites": prerequisites,
    }


def large_origin_fixture(pins):
    """Hand-declared synthetic stream with a full 3 MiB message, not observation.

    The reference intentionally repeats the synthetic artifact to exercise the
    two real validators. Production must NEVER derive its reference this way.
    Image/source/code labels below are synthetic hashes, not a code catalog.
    """
    image, source, code = digest("image"), digest("product source"), digest("code fingerprint")
    loaders = {kind: {"kind": kind, "image_sha256": image,
                       "loader_sha256": digest(kind + " loader"), "audit_sha256": digest(kind + " audit")}
               for kind in ("builtin", "source")}
    codes = {"product-code": {
        "kind": "source", "image_sha256": image, "source_sha256": source,
        "audit_sha256": digest("code audit"), "fingerprint_schema": origins.FINGERPRINT_SCHEMA,
        "codes": {code: {"filename": "/work/product.py", "function": "run", "lines": [7]}},
    }}
    records = {
        "b0": {"name": "builtins", "object_id": "builtin-object", "generation": 0,
               "kind": "builtin", "origin": "built-in", "source_sha256": None,
               "image_sha256": image, "loader_profile": "builtin", "observed_file": None,
               "spec_name": "builtins", "spec_origin": "built-in", "code_profile": None},
        "s0": {"name": "product", "object_id": "product-object", "generation": 0,
               "kind": "source", "origin": "/work/product.py", "source_sha256": source,
               "image_sha256": image, "loader_profile": "source", "observed_file": "/work/product.py",
               "spec_name": "product", "spec_origin": "/work/product.py", "code_profile": "product-code"},
    }
    events = [
        {"id": 1, "type": "start"},
        {"id": 2, "type": "create", "instance": "b0"},
        {"id": 3, "type": "import", "name": "builtins", "instance": "b0", "outcome": "bound"},
        {"id": 4, "type": "create", "instance": "s0"},
        {"id": 5, "type": "import", "name": "product", "instance": "s0", "outcome": "bound"},
        {"id": 6, "type": "phase-start", "phase_id": "call-1", "nodeid": "test_x.py::test_x", "when": "call"},
        {"id": 7, "type": "exception", "exception_id": "E", "phase_id": "call-1",
         "classes": [{"name": "builtins." + name, "instance": "b0"}
                     for name in ("ValueError", "Exception", "BaseException", "object")],
         "message": "m" * (3 * 1024 * 1024), "traceback_state": "present",
         "cause": None, "context": None, "children": [], "suppress_context": False},
        {"id": 8, "type": "frame", "exception_id": "E", "instance": "s0", "observed_module": "product",
         "binding_event_id": 5, "fingerprint_schema": origins.FINGERPRINT_SCHEMA,
         "fingerprint_sha256": code, "filename": "/work/product.py", "function": "run", "line": 7},
        {"id": 9, "type": "phase-end", "phase_id": "call-1", "outcome": "failed", "root_exception": "E"},
        {"id": 10, "type": "finish"},
    ]
    artifact = {"schema": origins.SCHEMA, "job_id": pins["job_id"], "nonce": pins["nonce"],
                "runtime_policy_sha256": pins["runtime_policy_sha256"], "catalog_sha256": pins["catalog_sha256"],
                "modules": {"instances": records, "final": {"builtins": "b0", "product": "s0"},
                            "live_instances": ["b0", "s0"], "negative_cache": []},
                "aliases": {}, "events": events}
    raw = origins.canonical(artifact) + b"\n"
    reference = origins.canonical({
        "schema": origins.REFERENCE_SCHEMA, "artifact_sha256": origin_sha256(raw),
        "artifact": artifact, "loader_profiles": loaders, "code_profiles": codes,
    }) + b"\n"
    return raw, reference


class FinalizationTests(unittest.TestCase):
    def setUp(self):
        self.pins = expected()
        self.report, self.origin = b"SYNTHETIC report", b"SYNTHETIC opaque origin"
        self.events = receipts(self.pins, self.report, self.origin)

    def completed(self, arm="base"):
        self.pins = expected(arm)
        self.events = receipts(self.pins, self.report, self.origin)
        lifecycle = Lifecycle(self.pins)
        with lifecycle:
            for event in self.events:
                lifecycle.advance(event)
        raw = lifecycle.artifact_bytes()
        host = host_fixture(self.pins, raw, self.events, self.report, self.origin)
        return lifecycle, raw, host

    def validate(self, raw, host):
        return validate_terminal(raw, expected_bindings=self.pins, report_raw=self.report,
                                 origin_raw=self.origin, after_exit=host)

    def assert_stage_poisoned(self, index, changes, diagnostic, arm="base"):
        # Establish the corresponding accepted synthetic fixture FIRST.
        _, raw, host = self.completed(arm)
        self.assertEqual(self.validate(raw, host)["status"], "transport-consistent-only")
        bad = copy.deepcopy(self.events[index])
        bad.update(changes)
        lifecycle = Lifecycle(self.pins)
        with self.assertRaisesRegex(FinalizationError, "^" + diagnostic + "$"):
            with lifecycle:
                for event in self.events[:index]:
                    lifecycle.advance(event)
                lifecycle.advance(bad)
        self.assertEqual(lifecycle.state, "FAILED")
        failure = decode(lifecycle.artifact_bytes())
        self.assertEqual(failure["state"], "FAILED")
        self.assertEqual(failure["failures"][0]["code"], "stage_rejected")
        with self.assertRaisesRegex(FinalizationError, "^inactive or failed lifecycle$"):
            lifecycle.advance(self.events[index])
        self.assertEqual(decode(lifecycle.artifact_bytes())["state"], "FAILED")

    def test_stage_sequence_nonincrease_isolated_and_permanent(self):
        for index in range(1, len(STAGES)):
            for sequence in (index * 10, index * 10 - 1):
                with self.subTest(index=index, sequence=sequence):
                    self.assert_stage_poisoned(index, {"sequence": sequence},
                                               "stage sequence not increasing")

    def test_premature_stage_pytest_status_isolated_and_permanent(self):
        for index in range(3):
            with self.subTest(index=index):
                self.assert_stage_poisoned(index, {"pytest_status": 1}, "pytest return not yet witnessed")

    def test_returned_stage_wrong_or_bool_pytest_status_isolated_and_permanent(self):
        for arm, wrong in (("base", 0), ("base", True), ("base", False),
                           ("gold", 1), ("gold", True), ("gold", False)):
            with self.subTest(arm=arm, wrong=wrong):
                self.assert_stage_poisoned(3, {"pytest_status": wrong},
                                           "pytest status differs from arm", arm)

    def test_premature_terminal_refs_isolated_and_permanent(self):
        for keys in (("report_sha256",), ("origin_sha256",), ("report_sha256", "origin_sha256")):
            with self.subTest(keys=keys):
                values = {"report_sha256": report_sha256(self.report),
                          "origin_sha256": origin_sha256(self.origin)}
                self.assert_stage_poisoned(3, {key: values[key] for key in keys},
                                           "terminal artifact references precede finalizing")

    def test_consistent_wrong_terminal_pair_reaches_raw_reference_guard(self):
        _, raw, host = self.completed()
        self.assertEqual(self.validate(raw, host)["status"], "transport-consistent-only")
        artifact = decode(raw)
        for stage in artifact["stages"][4:10]:
            stage["report_sha256"] = digest("consistently wrong report")
            stage["origin_sha256"] = digest("consistently wrong origin")
        raw = canonical(artifact)
        host["artifact_sha256"] = raw_sha256(raw)
        host["stages"] = copy.deepcopy(artifact["stages"])
        # Preserve HOST raw report/origin pins and prerequisite evidence exactly.
        self.assertEqual(host["report_sha256"], report_sha256(self.report))
        self.assertEqual(host["origin_sha256"], origin_sha256(self.origin))
        with self.assertRaisesRegex(FinalizationError, "^terminal raw references differ$"):
            self.validate(raw, host)

    def test_hash_entry_points_enforce_reviewed_inclusive_limits_and_exact_bytes(self):
        for function, limit, diagnostic in (
                (raw_sha256, MAX_RAW, "bounded raw bytes required"),
                (origin_sha256, MAX_ORIGIN_BYTES,
                 "origin payload bytes exceed reviewed budget or have invalid type"),
                (report_sha256, MAX_REPORT_BYTES,
                 "report payload bytes exceed reviewed budget or have invalid type")):
            with self.subTest(helper=function.__name__):
                raw = b"x" * limit
                self.assertEqual(function(raw), hashlib.sha256(raw).hexdigest())
                changed = raw[:-1] + b"y"
                self.assertEqual(function(changed), hashlib.sha256(changed).hexdigest())
                self.assertNotEqual(function(raw), function(changed))
                with self.assertRaisesRegex(FinalizationError, "^" + diagnostic + "$"):
                    function(raw + b"x")
                for invalid in (b"", bytearray(b"x"), memoryview(b"x"), "x", None, True):
                    with self.subTest(invalid_type=type(invalid).__name__):
                        with self.assertRaisesRegex(FinalizationError, "^" + diagnostic + "$"):
                            function(invalid)

    def test_envelope_json_budget_stays_two_mib(self):
        _, raw, host = self.completed()
        self.assertEqual(self.validate(raw, host)["status"], "transport-consistent-only")
        self.assertEqual(MAX_RAW, 2 * 1024 * 1024)
        with self.assertRaisesRegex(FinalizationError, "^artifact byte limit$"):
            canonical({"oversized_envelope": "x" * MAX_RAW})
        with self.assertRaisesRegex(FinalizationError, "^bounded raw bytes required$"):
            raw_sha256(b"x" * (MAX_RAW + 1))

    def test_parent_rejects_payload_limit_plus_one_at_specific_entry_point(self):
        for attribute, limit in (("origin", MAX_ORIGIN_BYTES), ("report", MAX_REPORT_BYTES)):
            with self.subTest(payload=attribute):
                self.report, self.origin = b"SYNTHETIC report", b"SYNTHETIC opaque origin"
                _, raw, host = self.completed()
                self.assertEqual(self.validate(raw, host)["status"], "transport-consistent-only")
                setattr(self, attribute, b"x" * (limit + 1))
                with self.assertRaisesRegex(FinalizationError,
                        "^" + attribute + " payload bytes exceed reviewed budget or have invalid type$"):
                    self.validate(raw, host)

    def test_full_32_mib_report_transport_is_opaque_not_report_validation(self):
        self.assertEqual(MAX_REPORT_BYTES, 32 * 1024 * 1024)
        # Deliberately NOT a valid report. This tests full-byte transport capacity;
        # actual report validation and prospective V6 schema remain OPEN.
        self.report = b"r" * MAX_REPORT_BYTES
        _, raw, host = self.completed()
        self.assertEqual(host["report_sha256"], hashlib.sha256(self.report).hexdigest())
        result = self.validate(raw, host)
        self.assertEqual(result["status"], "transport-consistent-only")
        self.assertFalse(result["candidate_acceptance_authorized"])
        self.assertFalse(result["host_authentication_established"])

    def test_three_mib_origin_passes_both_actual_consistency_components(self):
        self.assertEqual(MAX_ORIGIN_BYTES, origins.MAX_BYTES)
        self.assertEqual(MAX_ORIGIN_BYTES, 8 * 1024 * 1024)
        self.origin, reference_raw = large_origin_fixture(self.pins)
        self.assertGreater(len(self.origin), 3 * 1024 * 1024)
        self.assertLessEqual(len(self.origin), MAX_ORIGIN_BYTES)
        self.assertLessEqual(len(reference_raw), origins.MAX_BYTES)
        reference_pin = hashlib.sha256(reference_raw).hexdigest()
        origin_receipt = origins.validate_runtime_origins(
            self.origin, reference_raw=reference_raw, reference_sha256=reference_pin)
        self.assertEqual(origin_receipt["status"], "structurally-consistent-only")
        self.assertEqual(origin_receipt["artifact_sha256"], origin_sha256(self.origin))
        self.assertEqual(origin_receipt["reference_sha256"], reference_pin)
        self.assertEqual(origin_receipt["job_id"], self.pins["job_id"])
        self.assertEqual(origin_receipt["nonce"], self.pins["nonce"])
        self.events = receipts(self.pins, self.report, self.origin)
        # Join to the actual origin validator RESULT bytes, still under synthetic
        # fixture custody. This digest establishes no independent host evidence.
        self.events[5]["evidence_sha256"] = hashlib.sha256(
            origins.canonical(origin_receipt) + b"\n").hexdigest()
        lifecycle = Lifecycle(self.pins)
        with lifecycle:
            for event in self.events:
                lifecycle.advance(event)
        raw = lifecycle.artifact_bytes()
        host = host_fixture(self.pins, raw, self.events, self.report, self.origin)
        self.assertEqual(host["prerequisites"]["origin_validation_sha256"],
                         self.events[5]["evidence_sha256"])
        self.assertEqual(host["origin_sha256"], hashlib.sha256(self.origin).hexdigest())
        result = self.validate(raw, host)
        self.assertEqual(result["status"], "transport-consistent-only")
        self.assertEqual(result["pytest_status"], 1)
        self.assertFalse(result["candidate_acceptance_authorized"])
        self.assertFalse(result["host_authentication_established"])

    def test_base_pytest_one_is_distinct_from_zero_transport(self):
        _, raw, host = self.completed()
        result = self.validate(raw, host)
        self.assertEqual(result["status"], "transport-consistent-only")
        self.assertEqual(result["pytest_status"], 1)
        self.assertFalse(result["candidate_acceptance_authorized"])
        self.assertFalse(result["host_authentication_established"])

    def test_gold_requires_pytest_zero_and_zero_transport(self):
        _, raw, host = self.completed("gold")
        self.assertEqual(self.validate(raw, host)["pytest_status"], 0)

    def test_exact_single_use_state_sequence(self):
        lifecycle = Lifecycle(self.pins)
        self.assertEqual(lifecycle.state, "STARTUP")
        with lifecycle:
            for index, event in enumerate(self.events):
                lifecycle.advance(event)
                self.assertEqual(lifecycle.state, "STARTUP" if index == 0 else
                                 "RUNTIME" if index < 4 else "FINALIZING" if index < 9 else "FINALIZED")
        self.assertEqual(decode(lifecycle.artifact_bytes())["state"], "FINALIZED")

    def test_second_runtime_transition_permanently_fails(self):
        lifecycle = Lifecycle(self.pins)
        with self.assertRaisesRegex(FinalizationError, "stage order or multiplicity"):
            with lifecycle:
                lifecycle.advance(self.events[0])
                lifecycle.advance(self.events[1])
                lifecycle.advance(self.events[1])
        self.assertEqual(lifecycle.state, "FAILED")
        self.assertEqual(decode(lifecycle.artifact_bytes())["failures"][0]["code"], "stage_rejected")

    def test_every_transition_binds_exact_job_nonce_and_source_context(self):
        for index in range(len(STAGES)):
            for key in ("job_id", "nonce", "arm", "ordinal", *HASH_BINDINGS):
                with self.subTest(stage=STAGES[index], key=key):
                    lifecycle = Lifecycle(self.pins)
                    bad = copy.deepcopy(self.events[index])
                    bad["bindings"][key] = (
                        "OTHER-job" if key == "job_id" else "gold" if key == "arm"
                        else 1 if key == "ordinal" else digest("other-" + key))
                    with self.assertRaisesRegex(FinalizationError, "stage schema/bindings differ"):
                        with lifecycle:
                            for event in self.events[:index]:
                                lifecycle.advance(event)
                            lifecycle.advance(bad)
                    self.assertEqual(lifecycle.state, "FAILED")

    def test_transition_without_context_is_failure(self):
        lifecycle = Lifecycle(self.pins)
        with self.assertRaisesRegex(FinalizationError, "inactive or failed"):
            lifecycle.advance(self.events[0])
        self.assertEqual(lifecycle.state, "FAILED")
        with self.assertRaisesRegex(FinalizationError, "cannot be reentered"):
            with lifecycle:
                pass

    def test_missing_or_late_unconfigure_never_finalizes(self):
        lifecycle = Lifecycle(self.pins)
        with self.assertRaisesRegex(FinalizationError, "stage order or multiplicity"):
            with lifecycle:
                for event in self.events[:2]:
                    lifecycle.advance(event)
                lifecycle.advance(self.events[3])  # pytest_returned without unconfigure.
        self.assertEqual(lifecycle.state, "FAILED")

    def test_successful_draft_or_sessionfinish_alone_cannot_finalize(self):
        lifecycle = Lifecycle(self.pins)
        draft = {"session_finished": True, "exitstatus": 1}
        self.assertTrue(draft["session_finished"])  # Never supplied as lifecycle evidence.
        with self.assertRaisesRegex(FinalizationError, "did not finalize"):
            with lifecycle:
                for event in self.events[:2]:
                    lifecycle.advance(event)
        self.assertEqual(decode(lifecycle.artifact_bytes())["state"], "FAILED")

    def test_runtime_guard_failure_after_valid_draft_retains_failure(self):
        lifecycle = Lifecycle(self.pins)
        with self.assertRaisesRegex(FinalizationError, "explicit lifecycle failure"):
            with lifecycle:
                for event in self.events[:7]:
                    lifecycle.advance(event)
                lifecycle.abort("runtime_guard_passed", "guard_rejected",
                                evidence_sha256=digest("independent guard failure"))
        artifact = decode(lifecycle.artifact_bytes())
        self.assertEqual(artifact["state"], "FAILED")
        self.assertEqual(artifact["failures"][0]["code"], "guard_rejected")
        self.assertEqual(artifact["failures"][0]["evidence_sha256"],
                         digest("independent guard failure"))
        self.assertNotIn("finalized", [r["stage"] for r in artifact["stages"]])

    def test_cleanup_failure_has_no_success_artifact(self):
        lifecycle = Lifecycle(self.pins)
        with self.assertRaisesRegex(FinalizationError, "explicit lifecycle failure"):
            with lifecycle:
                for event in self.events[:8]:
                    lifecycle.advance(event)
                lifecycle.abort("cleanup_complete", "cleanup_failed",
                                evidence_sha256=digest("cleanup exception artifact"))
        self.assertEqual(decode(lifecycle.artifact_bytes())["state"], "FAILED")

    def test_terminal_exception_preserves_native_cause_context_types_and_propagates(self):
        lifecycle = Lifecycle(self.pins)
        root = ValueError("original failure")
        with self.assertRaises(ValueError) as caught:
            with lifecycle:
                lifecycle.advance(self.events[0])
                try:
                    raise TypeError("declared cause")
                except TypeError as cause:
                    raise root from cause
        self.assertIs(caught.exception, root)
        failure = decode(lifecycle.artifact_bytes())["failures"][0]
        self.assertEqual(failure["code"], "terminal_exception")
        self.assertEqual(failure["stage"], "runtime_entered")
        graph = failure["exception_types"]
        self.assertEqual(graph[0]["type"], "builtins.ValueError")
        self.assertEqual(graph[graph[0]["__cause__"]]["type"], "builtins.TypeError")
        self.assertEqual(graph[0]["__cause__"], graph[0]["__context__"])
        self.assertTrue(graph[0]["suppressed"])

    def test_startup_interrupt_and_system_exit_cannot_authorize_success(self):
        for error in (KeyboardInterrupt(), SystemExit(0)):
            with self.subTest(kind=type(error).__name__):
                lifecycle = Lifecycle(self.pins)
                with self.assertRaises(type(error)):
                    with lifecycle:
                        raise error
                artifact = decode(lifecycle.artifact_bytes())
                self.assertEqual(artifact["state"], "FAILED")
                self.assertEqual(artifact["failures"][0]["stage"], "startup_verified")

    def test_terminal_exception_diagnostics_never_format_user_exception(self):
        calls = []

        class Unformattable(Exception):
            def __str__(self):
                calls.append("str")
                raise AssertionError("exception formatter executed")

        lifecycle = Lifecycle(self.pins)
        with self.assertRaises(Unformattable):
            with lifecycle:
                raise Unformattable()
        self.assertEqual(decode(lifecycle.artifact_bytes())["state"], "FAILED")
        self.assertEqual(calls, [])

    def test_diagnostic_limit_never_emits_truncated_terminal_evidence(self):
        error = ValueError("first")
        for index in range(16):
            parent = ValueError("next")
            parent.__cause__ = error
            error = parent
        lifecycle = Lifecycle(self.pins)
        with self.assertRaisesRegex(FinalizationError, "exception diagnostic limit"):
            with lifecycle:
                raise error
        self.assertEqual(lifecycle.state, "FAILED")
        with self.assertRaisesRegex(FinalizationError, "failure diagnostics incomplete"):
            lifecycle.artifact_bytes()

    def test_caught_stage_error_cannot_reset_failed_state(self):
        lifecycle = Lifecycle(self.pins)
        with self.assertRaisesRegex(FinalizationError, "did not finalize"):
            with lifecycle:
                with self.assertRaises(FinalizationError):
                    lifecycle.advance(self.events[1])
                with self.assertRaisesRegex(FinalizationError, "inactive or failed"):
                    lifecycle.advance(self.events[0])
        self.assertEqual(lifecycle.state, "FAILED")

    def test_exception_after_finalized_receipt_still_poisoned_before_publication(self):
        lifecycle = Lifecycle(self.pins)
        with self.assertRaises(RuntimeError):
            with lifecycle:
                for event in self.events:
                    lifecycle.advance(event)
                raise RuntimeError("late terminal failure")
        artifact = decode(lifecycle.artifact_bytes())
        self.assertEqual(artifact["state"], "FAILED")
        self.assertEqual(artifact["failures"][0]["stage"], "after_finalized")

    def test_premature_terminal_artifact_attempt_poisoned_even_if_caught(self):
        lifecycle = Lifecycle(self.pins)
        with self.assertRaisesRegex(FinalizationError, "did not finalize"):
            with lifecycle:
                with self.assertRaisesRegex(FinalizationError, "before context exit"):
                    lifecycle.artifact_bytes()
        self.assertEqual(lifecycle.state, "FAILED")

    def test_context_cannot_reenter_after_finalization(self):
        lifecycle, _, _ = self.completed()
        with self.assertRaisesRegex(FinalizationError, "cannot be reentered"):
            with lifecycle:
                pass
        self.assertEqual(lifecycle.state, "FAILED")

    def test_receipts_and_bindings_are_copied_not_borrowed(self):
        pins = copy.deepcopy(self.pins)
        lifecycle = Lifecycle(pins)
        with lifecycle:
            for event in self.events:
                lifecycle.advance(event)
                event["evidence_sha256"] = digest("caller changed object")
            pins["nonce"] = digest("caller changed pins")
        artifact = decode(lifecycle.artifact_bytes())
        self.assertEqual(artifact["bindings"], self.pins)
        self.assertEqual(artifact["stages"][0]["evidence_sha256"], digest(STAGES[0]))

    def test_parent_rejects_transport_one_even_for_qualifying_base_status(self):
        for status in (1, 2, None, True):
            with self.subTest(status=status):
                _, raw, host = self.completed()
                host["process_exitstatus"] = status
                with self.assertRaisesRegex(FinalizationError, "transport exit must be zero"):
                    self.validate(raw, host)

    def test_parent_pytest_status_is_exact_integer_bound_to_arm(self):
        for arm, wrong in (("base", 0), ("base", True), ("gold", 1)):
            with self.subTest(arm=arm, wrong=wrong):
                _, raw, host = self.completed(arm)
                host["pytest_status"] = wrong
                with self.assertRaisesRegex(FinalizationError, "host pytest status differs"):
                    self.validate(raw, host)

    def test_parent_nonce_mismatch_cannot_be_signed_away(self):
        _, raw, host = self.completed()
        host["bindings"]["nonce"] = digest("other nonce")
        with self.assertRaisesRegex(FinalizationError, "terminal bindings differ"):
            self.validate(raw, host)

    def test_parent_checks_each_independent_raw_artifact_pin(self):
        for key in ("artifact_sha256", "report_sha256", "origin_sha256"):
            with self.subTest(key=key):
                _, raw, host = self.completed()
                host[key] = digest("different raw")
                with self.assertRaisesRegex(FinalizationError, "independent raw"):
                    self.validate(raw, host)

    def test_parent_reordered_or_extra_stages_cannot_hide_behind_matching_hash(self):
        for attack in ("reorder", "extra", "missing"):
            with self.subTest(attack=attack):
                _, raw, host = self.completed()
                artifact = decode(raw)
                stages = artifact["stages"]
                if attack == "reorder":
                    stages[2], stages[3] = stages[3], stages[2]
                elif attack == "extra":
                    stages.append(copy.deepcopy(stages[-1]))
                else:
                    del stages[2]
                host["stages"] = copy.deepcopy(stages)
                raw = canonical(artifact)
                host["artifact_sha256"] = raw_sha256(raw)
                with self.assertRaisesRegex(FinalizationError, "stage order or multiplicity|complete stage sequence"):
                    self.validate(raw, host)

    def test_host_stage_mismatch_including_bool_integer_alias_rejected(self):
        for attack in ("evidence", "boolean"):
            with self.subTest(attack=attack):
                _, raw, host = self.completed()
                if attack == "evidence":
                    host["stages"][5]["evidence_sha256"] = digest("other independent origin validation")
                else:
                    # Python equality considers True == 1, exact JSON must not.
                    host["stages"][3]["pytest_status"] = True
                with self.assertRaisesRegex(FinalizationError, "independent complete stage sequence"):
                    self.validate(raw, host)

    def test_after_exit_termination_cleanup_order_is_mandatory(self):
        for key in ("process_exit_sequence", "termination_sequence", "host_cleanup_sequence", "receipt_sequence"):
            for bad in (100, None, True):
                with self.subTest(key=key, bad=bad):
                    _, raw, host = self.completed()
                    host[key] = bad
                    with self.assertRaises(FinalizationError):
                        self.validate(raw, host)

    def test_every_independent_prerequisite_reference_is_required(self):
        for key in PREREQUISITES:
            with self.subTest(key=key):
                _, raw, host = self.completed()
                del host["prerequisites"][key]
                with self.assertRaisesRegex(FinalizationError, "prerequisite references required"):
                    self.validate(raw, host)

    def test_origin_report_guard_cleanup_validation_refs_match_exact_stage(self):
        for key in ("origin_validation_sha256", "report_validation_sha256",
                    "runtime_guard_sha256", "cleanup_sha256"):
            with self.subTest(key=key):
                _, raw, host = self.completed()
                host["prerequisites"][key] = digest("wrong validated artifact")
                with self.assertRaisesRegex(FinalizationError, "prerequisite/stage differs"):
                    self.validate(raw, host)

    def test_host_failure_after_producer_success_rejects_stale_success_bytes(self):
        _, raw, host = self.completed()
        host["failures"] = [{"stage": "host_cleanup", "cause": "retained cleanup failure"}]
        with self.assertRaisesRegex(FinalizationError, "failed or uncertain"):
            self.validate(raw, host)

    def test_failed_artifact_cannot_be_relabelled_by_zero_host_status(self):
        _, raw, host = self.completed()
        artifact = decode(raw)
        artifact["state"] = "FAILED"
        artifact["failures"] = [{"stage": "cleanup_complete", "code": "cleanup_failed"}]
        raw = canonical(artifact)
        host["artifact_sha256"] = raw_sha256(raw)
        with self.assertRaisesRegex(FinalizationError, "failed or uncertain"):
            self.validate(raw, host)

    def test_changed_finalization_report_or_origin_ref_poisoned(self):
        for key in ("report_sha256", "origin_sha256"):
            lifecycle = Lifecycle(self.pins)
            bad = copy.deepcopy(self.events[5])
            bad[key] = digest("changed after finalizing")
            with self.assertRaisesRegex(FinalizationError, "references changed"):
                with lifecycle:
                    for event in self.events[:5]:
                        lifecycle.advance(event)
                    lifecycle.advance(bad)
            self.assertEqual(lifecycle.state, "FAILED")

    def test_stage_evidence_reuse_cannot_collapse_independent_events(self):
        lifecycle = Lifecycle(self.pins)
        bad = copy.deepcopy(self.events[1])
        bad["evidence_sha256"] = self.events[0]["evidence_sha256"]
        with self.assertRaisesRegex(FinalizationError, "evidence reused"):
            with lifecycle:
                lifecycle.advance(self.events[0])
                lifecycle.advance(bad)

    def test_old_schema_extra_fields_and_noncanonical_bytes_reject(self):
        for attack in ("schema", "extra", "whitespace", "duplicate"):
            with self.subTest(attack=attack):
                _, raw, host = self.completed()
                artifact = decode(raw)
                if attack == "schema":
                    artifact["schema"] = "solcodex.control-phase-report.v5"
                    raw = canonical(artifact)
                elif attack == "extra":
                    artifact["process_exitstatus"] = 0
                    raw = canonical(artifact)
                elif attack == "whitespace":
                    raw += b" "
                else:
                    raw = raw.replace(b'"schema":"' + ARTIFACT_SCHEMA.encode() + b'"',
                                      b'"schema":"' + ARTIFACT_SCHEMA.encode() + b'","schema":"other"')
                host["artifact_sha256"] = raw_sha256(raw)
                with self.assertRaises(FinalizationError):
                    self.validate(raw, host)

    def test_unknown_binding_schema_and_boolean_ordinal_reject(self):
        for key, value in (("schema", "solcodex.control-phase-launch.v5"), ("ordinal", True)):
            pins = copy.deepcopy(self.pins)
            pins[key] = value
            with self.assertRaises(FinalizationError):
                Lifecycle(pins)
