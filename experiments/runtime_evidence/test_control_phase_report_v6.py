"""Prospective synthetic V6 phase-structure scenarios, not native attestations."""
import copy
import hashlib
import re
import unittest

import control_phase_report_v6 as component


def sha(label):
    return hashlib.sha256(label.encode("ascii")).hexdigest()


ORIGIN_SHA = sha("independently-retained-origin-bytes")


def fixture(arm="base", setup_failure=False):
    collected = ["tests/test_product.py::test_repair", "tests/test_product.py::test_non_tfp"]
    opaque = {"type": "builtins.AssertionError", "message": "", "traceback": []}
    failure_sha = component.raw_sha256(component.canonical(opaque))
    contracts, reports = [], []
    for index, node in enumerate(collected):
        failing = arm == "base" and index == 0
        when_failed = "setup" if setup_failure else "call"
        whens = ("setup", "teardown") if failing and setup_failure else component.PHASES
        phases = []
        for when in whens:
            failed = failing and when == when_failed
            phases.append({"when": when, "outcome": "failed" if failed else "passed",
                           "failure_raw_sha256": failure_sha if failed else None})
            reports.append({"nodeid": node, "when": when,
                            "outcome": "failed" if failed else "passed", "wasxfail": None,
                            "failure": copy.deepcopy(opaque) if failed else None})
        contracts.append({"nodeid": node, "phases": phases})
    launch = {"schema": component.LAUNCH_SCHEMA, "job_id": "job-original-1", "nonce": sha("nonce"),
              "purpose": "candidate" if arm == "candidate" else "control", "arm": arm, "ordinal": 0,
              "profile": {"python_version": "3.10.19", "pytest_version": "9.0.2", "image_sha256": sha("image")},
              "bindings": {k: sha(k) for k in component.HASH_BINDINGS}, "work_root": "/work",
              "collected": collected, "FAIL_TO_PASS": [collected[0]], "phase_contracts": contracts}
    report = {"schema": component.REPORT_SCHEMA, "launch_raw_sha256": None,
              **{k: copy.deepcopy(launch[k]) for k in
                 ("job_id", "nonce", "purpose", "arm", "ordinal", "profile", "bindings", "work_root")},
              "session_started": True, "collection_finished": True, "session_finished": True,
              "pytest_status": 1 if arm == "base" else 0, "collection_errors": [],
              "infrastructure_errors": [], "collected": list(collected), "origin_raw_sha256": ORIGIN_SHA,
              "reports": reports}
    return launch, report


def freeze(launch, report):
    raw = component.canonical(launch)
    expected = component.raw_sha256(raw)
    report = copy.deepcopy(report)
    report["launch_raw_sha256"] = expected
    return raw, expected, component.canonical(report)


class ReportStructureTests(unittest.TestCase):
    def assess(self, launch, report):
        launch_raw, expected, report_raw = freeze(launch, report)
        return component.assess_report(report_raw, launch_raw=launch_raw,
                                       expected_launch_sha256=expected, expected_origin_sha256=ORIGIN_SHA)

    def reject_report(self, change, diagnostic, arm="base", setup_failure=False):
        launch, report = fixture(arm, setup_failure)
        self.assertEqual(self.assess(launch, report)["status"], "STRUCTURAL_REPORT_MATCH_ONLY")
        change(report)
        with self.assertRaisesRegex(component.ReportStructureError, "^" + re.escape(diagnostic) + "$"):
            self.assess(launch, report)

    def reject_launch(self, change, diagnostic, arm="base"):
        launch, report = fixture(arm)
        self.assertEqual(self.assess(launch, report)["status"], "STRUCTURAL_REPORT_MATCH_ONLY")
        change(launch)
        with self.assertRaisesRegex(component.ReportStructureError, "^" + re.escape(diagnostic) + "$"):
            self.assess(launch, report)

    def test_base_pytest_one_does_not_claim_transport(self):
        result = self.assess(*fixture())
        self.assertEqual(result["pytest_status"], 1)
        self.assertFalse(result["transport_observed"])
        self.assertFalse(result["controls_qualified"])

    def test_gold_all_phases_pass(self):
        result = self.assess(*fixture("gold"))
        self.assertEqual(result["pytest_status"], 0)
        self.assertEqual(result["phase_count"], 6)

    def test_candidate_all_phases_pass_without_acceptance_authority(self):
        result = self.assess(*fixture("candidate"))
        self.assertEqual(result["phase_count"], 6)
        self.assertFalse(result["candidate_acceptance_authorized"])

    def test_failed_setup_does_not_invent_call(self):
        result = self.assess(*fixture(setup_failure=True))
        self.assertEqual(result["phase_count"], 5)
        self.assertEqual(result["opaque_failure_bindings"][0]["when"], "setup")

    def test_original_native_unit_profile_is_separate(self):
        launch, report = fixture("gold")
        for item in (launch, report):
            item["profile"]["python_version"] = "3.12.3"
            item["profile"]["pytest_version"] = "8.3.5"
        self.assertEqual(self.assess(launch, report)["phase_count"], 6)

    def test_opaque_failure_match_is_not_semantic_validation(self):
        result = self.assess(*fixture())
        self.assertFalse(result["failure_content_validated"])
        self.assertFalse(result["origin_content_validated"])
        self.assertFalse(result["host_authentication_established"])

    def test_later_ordinal_is_not_first_three_qualification(self):
        launch, report = fixture()
        launch["ordinal"] = report["ordinal"] = 17
        self.assertFalse(self.assess(launch, report)["controls_qualified"])

    def test_v5_report_is_rejected(self):
        self.reject_report(lambda r: r.update(schema="solcodex.control-phase-report.v5"), "report-schema-v6-required")

    def test_v5_launch_is_rejected(self):
        self.reject_launch(lambda l: l.update(schema="solcodex.control-phase-launch.v5"), "launch-schema-v6-required")

    def test_candidate_cannot_be_relabelled_gold(self):
        self.reject_launch(lambda l: l.update(arm="gold"), "purpose-arm", arm="candidate")

    def test_launch_bool_ordinal_is_rejected(self):
        self.reject_launch(lambda l: l.update(ordinal=True), "ordinal-type-or-range")

    def test_report_bool_ordinal_does_not_equal_integer_one(self):
        launch, report = fixture()
        launch["ordinal"] = report["ordinal"] = 1
        self.assertEqual(self.assess(launch, report)["phase_count"], 6)
        report["ordinal"] = True
        with self.assertRaisesRegex(component.ReportStructureError, "^report-binding-ordinal$"):
            self.assess(launch, report)

    def test_profile_cannot_use_hosted_ci_python(self):
        self.reject_launch(lambda l: l["profile"].update(python_version="3.10.21"), "original-profile-unresolved")

    def test_profile_pair_cannot_be_crossed(self):
        self.reject_launch(lambda l: l["profile"].update(pytest_version="8.3.5"), "original-profile-unresolved")

    def test_report_binding_hash_drift(self):
        self.reject_report(lambda r: r["bindings"].update(catalog_sha256=sha("other")), "report-binding-bindings")

    def test_launch_missing_binding(self):
        self.reject_launch(lambda l: l["bindings"].pop("input_sha256"), "bindings-fields")

    def test_report_extra_transport_field_is_not_an_exit_observation(self):
        self.reject_report(lambda r: r.update(transport_status=0), "report-fields")

    def test_report_pytest_status_is_not_transport_zero(self):
        self.reject_report(lambda r: r.update(pytest_status=0), "pytest-status")

    def test_report_bool_pytest_status_is_rejected(self):
        self.reject_report(lambda r: r.update(pytest_status=True), "pytest-status")

    def test_incomplete_session(self):
        self.reject_report(lambda r: r.update(session_finished=False), "incomplete-session_finished")

    def test_collection_errors_veto(self):
        self.reject_report(lambda r: r["collection_errors"].append("import failure"), "veto-collection_errors")

    def test_infrastructure_errors_veto(self):
        self.reject_report(lambda r: r["infrastructure_errors"].append("resource failure"), "veto-infrastructure_errors")

    def test_collection_order_is_preserved(self):
        self.reject_report(lambda r: r["collected"].reverse(), "collection-order-or-content")

    def test_duplicate_collected_node(self):
        self.reject_report(lambda r: r["collected"].append(r["collected"][0]), "duplicate-node")

    def test_missing_phase(self):
        self.reject_report(lambda r: r["reports"].pop(), "phase-count")

    def test_duplicate_phase(self):
        self.reject_report(lambda r: r["reports"].append(copy.deepcopy(r["reports"][0])), "phase-count")

    def test_reordered_phase(self):
        self.reject_report(lambda r: r["reports"].reverse(), "phase-order-or-multiplicity")

    def test_failed_setup_cannot_gain_synthetic_call(self):
        self.reject_report(lambda r: r["reports"].insert(1, {"nodeid": r["collected"][0], "when": "call",
                           "outcome": "passed", "wasxfail": None, "failure": None}), "phase-count", setup_failure=True)

    def test_skipped_phase_is_not_pass(self):
        self.reject_report(lambda r: r["reports"][0].update(outcome="skipped"), "phase-outcome-or-xfail")

    def test_xfail_marker_is_vetoed(self):
        self.reject_report(lambda r: r["reports"][0].update(wasxfail="known bug"), "phase-outcome-or-xfail")

    def test_passed_phase_cannot_carry_failure(self):
        self.reject_report(lambda r: r["reports"][0].update(failure={"message": ""}), "passed-phase-failure")

    def test_failure_hash_covers_empty_message_change(self):
        self.reject_report(lambda r: r["reports"][1]["failure"].update(message="different"), "failure-full-byte-digest")

    def test_failed_phase_requires_opaque_object(self):
        self.reject_report(lambda r: r["reports"][1].update(failure=None), "failure-opaque-object")

    def test_non_tfp_base_contract_must_pass(self):
        def change(launch):
            launch["phase_contracts"][1]["phases"][1].update(outcome="failed", failure_raw_sha256=sha("failure"))
        self.reject_launch(change, "base-tfp-or-all-phase-pass-contract")

    def test_gold_contract_cannot_declare_failure(self):
        def change(launch):
            launch["phase_contracts"][0]["phases"][1].update(outcome="failed", failure_raw_sha256=sha("failure"))
        self.reject_launch(change, "base-tfp-or-all-phase-pass-contract", arm="gold")

    def test_candidate_contract_cannot_declare_failure(self):
        def change(launch):
            launch["phase_contracts"][0]["phases"][1].update(outcome="failed", failure_raw_sha256=sha("failure"))
        self.reject_launch(change, "base-tfp-or-all-phase-pass-contract", arm="candidate")

    def test_launch_cannot_omit_call_after_passed_setup(self):
        self.reject_launch(lambda l: l["phase_contracts"][0]["phases"].pop(1), "honest-setup-phase-protocol")

    def test_origin_digest_is_independently_supplied(self):
        self.reject_report(lambda r: r.update(origin_raw_sha256=sha("different-origin")), "origin-raw-binding")

    def test_wrong_independent_launch_digest(self):
        launch_raw, expected, report_raw = freeze(*fixture())
        component.assess_report(report_raw, launch_raw=launch_raw, expected_launch_sha256=expected,
                                expected_origin_sha256=ORIGIN_SHA)
        with self.assertRaisesRegex(component.ReportStructureError, "^launch-raw-sha256$"):
            component.assess_report(report_raw, launch_raw=launch_raw, expected_launch_sha256=sha("other-launch"),
                                    expected_origin_sha256=ORIGIN_SHA)

    def test_noncanonical_report_bytes_are_rejected(self):
        launch_raw, expected, report_raw = freeze(*fixture())
        component.assess_report(report_raw, launch_raw=launch_raw, expected_launch_sha256=expected,
                                expected_origin_sha256=ORIGIN_SHA)
        with self.assertRaisesRegex(component.ReportStructureError, "^json-canonical-ascii-lf$"):
            component.assess_report(report_raw + b"\n", launch_raw=launch_raw,
                                    expected_launch_sha256=expected, expected_origin_sha256=ORIGIN_SHA)

    def test_duplicate_json_key_is_rejected(self):
        self.assertEqual(component.decode(b'{"x":1}\n'), {"x": 1})
        with self.assertRaisesRegex(component.ReportStructureError, "^json-duplicate-key$"):
            component.decode(b'{"x":1,"x":1}\n')

    def test_ascii_lf_raw_hash_is_not_no_lf_object_hash(self):
        value = {"message": "данные"}
        raw = component.canonical(value)
        self.assertEqual(component.decode(raw), value)
        self.assertNotEqual(component.raw_sha256(raw), hashlib.sha256(raw[:-1]).hexdigest())


if __name__ == "__main__":
    unittest.main()
