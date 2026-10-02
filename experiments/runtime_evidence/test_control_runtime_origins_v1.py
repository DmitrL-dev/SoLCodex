"""Synthetic artifact consistency scenarios; no native/host attestation.

No live module/host/event authentication is asserted. Expected artifacts below
are deliberately hand-declared test contracts, NOT a way to create production
references from observer output. No compiler/observer code is imported here.
"""

import copy
import hashlib
import json
import unittest

import control_runtime_origins_v1 as origins


def pin(label):
    return hashlib.sha256(("SYNTHETIC:" + label).encode()).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")


def fixture():
    image = pin("original-image-no-upgrade")
    loaders = {kind: {"kind": kind, "image_sha256": image,
                       "loader_sha256": pin(kind + "-loader"), "audit_sha256": pin(kind + "-audit")}
               for kind in ("source", "native", "builtin", "frozen", "direct-script-main")}
    codes = {}

    def record(name, obj, kind, origin, profile=None, generation=0):
        source = None if kind in ("builtin", "frozen") else pin(profile or origin)
        if profile is not None:
            codes[profile] = {"kind": "code-image" if kind == "frozen" else "source",
                              "image_sha256": image, "source_sha256": source,
                              "audit_sha256": pin("code-audit-" + profile),
                              "fingerprint_schema": origins.FINGERPRINT_SCHEMA,
                              "codes": {pin(profile): {"filename": "<frozen frozenpkg>" if kind == "frozen" else origin,
                                                       "function": "run", "lines": [7, 8]}}}
        return {"name": name, "object_id": obj, "generation": generation, "kind": kind,
                "origin": origin, "source_sha256": source, "image_sha256": image,
                "loader_profile": kind, "observed_file": None if kind == "builtin" else origin,
                "spec_name": None if kind == "direct-script-main" else name,
                "spec_origin": None if kind == "direct-script-main" else origin, "code_profile": profile}

    records = {"b0": record("builtins", "obj-b", "builtin", "built-in"),
               "f0": record("frozenpkg", "obj-f", "frozen", "frozen", "frozen-code"),
               "m0": record("__main__", "obj-m", "direct-script-main", "/tools/runner.py", "main-code"),
               "s0": record("alpha", "obj-s", "source", "/work/é\u2028source.py", "source-v0"),
               "s1": record("alpha", "obj-s", "source", "/work/é\u2028source.py", "source-v1", 1),
               "n0": record("nativepkg", "obj-n", "native", "/runtime/nativepkg.so"),
               "t0": record("transient", "obj-t", "source", "/work/transient.py", "transient-code")}
    records["b0"]["observed_file"] = "/literal/incidental-builtins.py"
    records["f0"]["observed_file"] = "/runtime/Lib/frozenpkg.py"
    events, bindings = [], {}

    def add(kind, **values):
        events.append({"id": len(events) + 1, "type": kind, **values})
        return len(events)

    def exception(exc, state, message, cause=None):
        add("exception", exception_id=exc, phase_id="call-1", classes=[
            {"name": "builtins." + name, "instance": "b0"}
            for name in ("ValueError", "Exception", "BaseException", "object")],
            message=message, traceback_state=state, cause=cause, context=cause,
            children=[], suppress_context=cause is not None)

    def frame(instance, binding, observed):
        profile = records[instance]["code_profile"]
        code = codes[profile]["codes"][pin(profile)]
        add("frame", exception_id="E", instance=instance, observed_module=observed,
            binding_event_id=binding, fingerprint_schema=origins.FINGERPRINT_SCHEMA,
            fingerprint_sha256=pin(profile), filename=code["filename"], function="run", line=7)

    add("start")
    for instance in ("b0", "f0", "m0", "s0", "n0", "t0"):
        add("create", instance=instance)
        bindings[instance] = add("import", name=records[instance]["name"], instance=instance, outcome="bound")
    alias = add("alias-set", name="short", instance="s0")
    add("alias-set", name="bootstrap", instance="m0")
    add("negative-cache-set", name="optional")
    add("import", name="optional", instance=None, outcome="negative")
    add("negative-cache-resolve", name="optional")
    add("import", name="optional", instance=None, outcome="missing")
    add("phase-start", phase_id="setup-1", nodeid="tests/test_x.py::test_x", when="setup")
    add("phase-end", phase_id="setup-1", outcome="passed", root_exception=None)
    add("phase-start", phase_id="call-1", nodeid="tests/test_x.py::test_x", when="call")
    exception("E", "present", "\0 semantic \n", "C")
    exception("C", "none", "")
    frame("s0", alias, "short")
    frame("f0", bindings["f0"], "frozenpkg")
    frame("m0", bindings["m0"], "__main__")
    add("unload", name="transient", instance="t0")
    add("retire", instance="t0")
    frame("t0", bindings["t0"], "transient")  # Historical traceback survives unload/retire.
    reloaded = add("reload", name="alpha", old_instance="s0", instance="s1")
    frame("s1", reloaded, "short")
    frame("s1", reloaded, "short")  # Distinct frame events, identical code/location.
    add("phase-end", phase_id="call-1", outcome="failed", root_exception="E")
    add("phase-start", phase_id="teardown-1", nodeid="tests/test_x.py::test_x", when="teardown")
    add("phase-end", phase_id="teardown-1", outcome="passed", root_exception=None)
    add("alias-remove", name="short", instance="s1")
    add("alias-set", name="short", instance="s1")
    add("negative-cache-set", name="pending")
    add("finish")
    artifact = {"schema": origins.SCHEMA, "job_id": "SYNTHETIC-job", "nonce": pin("nonce"),
                "runtime_policy_sha256": pin("runtime-policy"), "catalog_sha256": pin("catalog"),
                "modules": {"instances": records,
                            "final": {"builtins": "b0", "frozenpkg": "f0", "__main__": "m0", "alpha": "s1", "nativepkg": "n0"},
                            "live_instances": ["b0", "f0", "m0", "n0", "s1"], "negative_cache": ["pending"]},
                "aliases": {"short": "alpha", "bootstrap": "__main__"}, "events": events}
    return artifact, loaders, codes


def reference(artifact, loaders, codes):
    # SYNTHETIC TEST ONLY. Production caller must obtain the whole expected
    # artifact independently; re-sealing observer bytes is not authentication.
    return encoded({"schema": origins.REFERENCE_SCHEMA,
                    "artifact_sha256": hashlib.sha256(encoded(artifact)).hexdigest(),
                    "artifact": artifact, "loader_profiles": loaders, "code_profiles": codes})


class RuntimeOriginsTests(unittest.TestCase):
    def setUp(self):
        self.artifact, self.loaders, self.codes = fixture()
        self.reference = reference(self.artifact, self.loaders, self.codes)

    def check(self, artifact=None, reference_raw=None):
        raw = self.reference if reference_raw is None else reference_raw
        return origins.validate_runtime_origins(encoded(self.artifact if artifact is None else artifact),
                                                reference_raw=raw, reference_sha256=hashlib.sha256(raw).hexdigest())

    def event(self, kind):
        return next(e for e in self.artifact["events"] if e["type"] == kind)

    def rebound_veto(self, diagnostic):
        # A self-consistent attacker reference cannot bypass internal replay.
        raw = reference(self.artifact, self.loaders, self.codes)
        with self.assertRaisesRegex(origins.OriginError, "^" + diagnostic + "$"):
            self.check(reference_raw=raw)

    def append_before_finish(self, *events):
        finish = self.artifact["events"].pop()
        for event in events:
            self.artifact["events"].append({"id": len(self.artifact["events"]) + 1, **event})
        finish["id"] = len(self.artifact["events"]) + 1
        self.artifact["events"].append(finish)

    def insert_event(self, index, event):
        # Resealed chronology attacks must not fail on shifted frame witnesses.
        events = self.artifact["events"]
        events.insert(index, event)
        old_ids = {entry["id"]: i for i, entry in enumerate(events, 1) if "id" in entry}
        for i, entry in enumerate(events, 1):
            if entry["type"] == "frame":
                entry["binding_event_id"] = old_ids[entry["binding_event_id"]]
            entry["id"] = i

    def test_full_typed_stream_is_consistency_only(self):
        result = self.check()
        self.assertEqual(result["status"], "structurally-consistent-only")
        self.assertEqual(set(result), {"schema", "status", "artifact_sha256", "reference_sha256", "job_id", "nonce"})
        self.assertNotIn("transient", self.artifact["modules"]["final"])
        self.assertIn("t0", self.artifact["modules"]["instances"])

    def test_embedded_sentinels_do_not_use_incidental_file(self):
        self.check()
        for instance in ("b0", "f0"):
            with self.subTest(instance=instance):
                self.setUp()
                record = self.artifact["modules"]["instances"][instance]
                record["origin"] = record["spec_origin"] = record["observed_file"]
                self.rebound_veto("embedded origin/source differs")

    def test_direct_script_missing_spec_is_literal(self):
        self.check()
        self.artifact["modules"]["instances"]["m0"]["spec_name"] = "__main__"
        self.rebound_veto("direct script must retain None spec")

    def test_alias_frame_requires_exact_historical_identity(self):
        self.check()
        self.event("frame")["observed_module"] = "bootstrap"
        self.rebound_veto("frame historical binding differs")

    def test_alias_chain_cycle_and_absent_targets_reject(self):
        for target in ("short", "bootstrap", "not_loaded"):
            with self.subTest(target=target):
                self.setUp()
                self.artifact["aliases"]["short"] = target
                self.rebound_veto("alias chain or absent canonical target")

    def test_event_ids_reject_gap_duplicate_reorder_and_bool(self):
        for attack in ("gap", "duplicate", "reorder", "bool"):
            with self.subTest(attack=attack):
                self.setUp()
                events = self.artifact["events"]
                if attack == "reorder":
                    events[1], events[2] = events[2], events[1]
                else:
                    events[1]["id"] = {"gap": 3, "duplicate": 1, "bool": True}[attack]
                self.rebound_veto("invalid integer" if attack == "bool" else "event IDs skipped reordered duplicated")

    def test_renumbered_omission_and_extra_import_do_not_match_reference(self):
        for attack in ("omit", "extra"):
            with self.subTest(attack=attack):
                self.setUp()
                events = self.artifact["events"]
                if attack == "omit":
                    # Removing a cache-hit-like missing import preserves final state.
                    events.remove(next(e for e in events if e["type"] == "import" and e["outcome"] == "missing"))
                else:
                    events.insert(-1, {"type": "import", "name": "builtins", "instance": "b0", "outcome": "bound"})
                # Repair numeric IDs AND frame links to isolate full-stream equality.
                old_ids = {e.get("id"): i for i, e in enumerate(events, 1) if "id" in e}
                for i, event in enumerate(events, 1):
                    event["id"] = i
                    if event["type"] == "frame":
                        event["binding_event_id"] = old_ids[event["binding_event_id"]]
                with self.assertRaisesRegex(origins.OriginError, "^complete independent artifact differs$"):
                    self.check()

    def test_identical_frame_multiplicity_is_not_a_set(self):
        self.check()
        events = self.artifact["events"]
        last = [e for e in events if e["type"] == "frame"][-1]
        events.remove(last)
        for i, event in enumerate(events, 1):
            event["id"] = i  # All binding witnesses precede this removal.
        with self.assertRaisesRegex(origins.OriginError, "^complete independent artifact differs$"):
            self.check()

    def test_transient_inventory_and_lifetimes_cannot_be_dropped(self):
        self.check()
        del self.artifact["modules"]["instances"]["t0"]
        self.rebound_veto("unknown instance")

    def test_reload_requires_new_generation_same_object(self):
        for field, value in (("generation", 0), ("object_id", "other-object")):
            with self.subTest(field=field):
                self.setUp()
                self.artifact["modules"]["instances"]["s1"][field] = value
                self.rebound_veto("reload generation differs")

    def test_instance_token_cannot_be_reused(self):
        self.check()
        self.append_before_finish({"type": "create", "instance": "b0"})
        self.rebound_veto("instance/object reused")

    def test_replacement_preserves_old_alias_until_explicit_rebind(self):
        self.check()
        records = self.artifact["modules"]["instances"]
        records["s2"] = {**records["s1"], "object_id": "obj-replacement", "generation": 0}
        self.append_before_finish({"type": "create", "instance": "s2"},
                                  {"type": "replace", "name": "alpha", "old_instance": "s1", "instance": "s2"})
        self.artifact["modules"]["final"]["alpha"] = "s2"
        self.artifact["modules"]["live_instances"].append("s2")
        self.rebound_veto("final alias canonical object absent")
        self.append_before_finish({"type": "alias-rebind", "name": "short", "old_instance": "s1", "instance": "s2"},
                                  {"type": "retire", "instance": "s1"})
        self.artifact["modules"]["live_instances"].remove("s1")
        result = self.check(reference_raw=reference(self.artifact, self.loaders, self.codes))
        self.assertEqual(result["status"], "structurally-consistent-only")

    def test_unload_can_leave_alias_alive_but_retire_cannot(self):
        self.append_before_finish({"type": "unload", "name": "alpha", "instance": "s1"},
                                  {"type": "import", "name": "short", "instance": "s1", "outcome": "bound"},
                                  {"type": "import", "name": "alpha", "instance": "s1", "outcome": "bound"})
        self.check(reference_raw=reference(self.artifact, self.loaders, self.codes))
        self.append_before_finish({"type": "retire", "instance": "s1"})
        self.rebound_veto("retire bound or nonlive instance")

    def test_retire_alias_only_live_instance_before_canonical_reimport(self):
        # At retire: s1 is live, alpha is absent, short still binds s1.
        # Only the alias predicate in the retirement guard is false.
        self.append_before_finish({"type": "unload", "name": "alpha", "instance": "s1"},
                                  {"type": "import", "name": "short", "instance": "s1", "outcome": "bound"},
                                  {"type": "retire", "instance": "s1"},
                                  {"type": "import", "name": "alpha", "instance": "s1", "outcome": "bound"})
        self.rebound_veto("retire bound or nonlive instance")

    def test_negative_cache_resolution_and_final_cache_are_explicit(self):
        self.check()
        self.event("negative-cache-resolve")["name"] = "missing"
        self.rebound_veto("negative cache resolution without entry")
        self.setUp()
        self.artifact["modules"]["negative_cache"] = []
        self.rebound_veto("final module/cache state differs")

    def test_unknown_events_and_uncertainty_never_become_coverage(self):
        self.event("import")["type"] = "uncertainty"
        self.rebound_veto("unknown or unresolved event")
        self.setUp()
        self.event("phase-end")["outcome"] = "unknown"
        self.rebound_veto("uncertain phase outcome")

    def test_wrong_image_source_and_loader_profiles_reject(self):
        for field, value, diagnostic in (("image_sha256", pin("wrong-image"), "loader/image profile differs"),
                                         ("source_sha256", pin("wrong-source"), "code source binding differs"),
                                         ("loader_profile", "frozen", "loader/image profile differs")):
            with self.subTest(field=field):
                self.setUp()
                self.artifact["modules"]["instances"]["s0"][field] = value
                self.rebound_veto(diagnostic)

    def test_frozen_frame_requires_explicit_audited_code_image(self):
        self.artifact["modules"]["instances"]["f0"]["code_profile"] = None
        self.rebound_veto("frame lacks audited code profile")
        self.setUp()
        self.codes["frozen-code"]["kind"] = "source"
        self.codes["frozen-code"]["source_sha256"] = pin("invented-source")
        self.rebound_veto("sourceless frame needs audited code image")

    def test_native_python_frame_is_not_authenticated_by_binary_hash(self):
        frame = next(e for e in self.artifact["events"] if e["type"] == "frame" and e["instance"] == "f0")
        frame["instance"], frame["observed_module"] = "n0", "nativepkg"
        frame["binding_event_id"] = next(e["id"] for e in self.artifact["events"]
                                         if e["type"] == "import" and e["instance"] == "n0")
        self.rebound_veto("frame lacks audited code profile")
        # Separate explicit code-image audit, never the .so SHA as a fingerprint.
        self.codes["native-code"] = copy.deepcopy(self.codes["frozen-code"])
        self.codes["native-code"]["audit_sha256"] = pin("independent-native-code-image-audit")
        self.artifact["modules"]["instances"]["n0"]["code_profile"] = "native-code"
        self.check(reference_raw=reference(self.artifact, self.loaders, self.codes))
        binary_sha = self.artifact["modules"]["instances"]["n0"]["source_sha256"]
        self.assertNotIn(binary_sha, self.codes["native-code"]["codes"])
        frame["fingerprint_sha256"] = binary_sha
        self.rebound_veto("frame fingerprint differs")

    def test_frame_code_fingerprint_and_locations_are_exact(self):
        for field, value, diagnostic in (("fingerprint_sha256", pin("wrong-code"), "frame fingerprint differs"),
                                         ("fingerprint_schema", "old-marshal", "frame fingerprint differs"),
                                         ("filename", "/work/other.py", "frame code locations differ"),
                                         ("function", "other", "frame code locations differ"),
                                         ("line", 9, "frame code locations differ")):
            with self.subTest(field=field):
                self.setUp()
                self.event("frame")[field] = value
                self.rebound_veto(diagnostic)

    def test_exception_graph_cycles_missing_edges_and_frame_state_reject(self):
        for attack, diagnostic in (("cycle", "cyclic exception graph"),
                                    ("missing", "unresolved exception edge"),
                                    ("mro", "exception MRO differs"),
                                    ("state", "traceback state/frame mismatch")):
            with self.subTest(attack=attack):
                self.setUp()
                child = next(e for e in self.artifact["events"] if e["type"] == "exception" and e["exception_id"] == "C")
                if attack == "mro":
                    child["classes"].pop(-2)
                elif attack == "state":
                    child["traceback_state"] = "present"
                else:
                    child["cause"] = "E" if attack == "cycle" else "missing"
                self.rebound_veto(diagnostic)

    def test_empty_whitespace_nul_messages_are_not_normalized(self):
        for message in ("", " ", "\t\n", "\0", " x\0 "):
            with self.subTest(message=message):
                self.setUp()
                self.event("exception")["message"] = message
                self.check(reference_raw=reference(self.artifact, self.loaders, self.codes))
                with self.assertRaisesRegex(origins.OriginError, "^complete independent artifact differs$"):
                    self.check()

    def test_import_error_has_exact_exception_scope(self):
        # Reuse the actual synthetic observed root, preserving its multiplicity
        # as BOTH the failed phase and an import error within that phase.
        events = self.artifact["events"]
        index = next(i for i, e in enumerate(events) if e["type"] == "phase-end" and e["phase_id"] == "call-1")
        events.insert(index, {"type": "import-error", "name": "alpha", "instance": "s1", "exception_id": "E"})
        for i, event in enumerate(events, 1):
            event["id"] = i  # All frame binding witnesses precede this insertion.
        self.check(reference_raw=reference(self.artifact, self.loaders, self.codes))
        self.event("import-error")["exception_id"] = "absent"
        self.rebound_veto("invalid root exception")

    def test_import_error_existing_exception_from_other_scope_rejects(self):
        self.check()
        events = self.artifact["events"]
        index = next(i for i, e in enumerate(events)
                     if e["type"] == "phase-end" and e["phase_id"] == "teardown-1")
        # E exists with present traceback in call-1; alpha/s1 is valid here.
        # Only E's scope differs from the active teardown-1 scope.
        self.insert_event(index, {"type": "import-error", "name": "alpha",
                                  "instance": "s1", "exception_id": "E"})
        self.rebound_veto("invalid root exception")

    def test_root_markers_after_declaration_preserve_forward_graph_edges(self):
        events = self.artifact["events"]
        index = next(i for i, e in enumerate(events) if e["type"] == "exception")
        # E already has forward cause/context C; exercise forward children too.
        events[index]["children"] = ["C"]
        self.insert_event(index + 1, {"type": "exception-observed", "exception_id": "E"})
        self.insert_event(index + 2, {"type": "import-error", "name": "alpha",
                                      "instance": "s0", "exception_id": "E"})
        # C and E's frames follow both markers. These are declarations, not
        # timestamps claiming when the native exceptions were raised.
        self.check(reference_raw=reference(self.artifact, self.loaders, self.codes))

    def test_root_observation_before_exception_declaration_rejects(self):
        events = self.artifact["events"]
        index = next(i for i, e in enumerate(events) if e["type"] == "exception")
        self.insert_event(index, {"type": "exception-observed", "exception_id": "E"})
        self.rebound_veto("invalid root exception")

    def test_import_error_before_exception_declaration_rejects(self):
        events = self.artifact["events"]
        index = next(i for i, e in enumerate(events) if e["type"] == "exception")
        # alpha/s0 is already created and bound, isolating missing declaration.
        self.insert_event(index, {"type": "import-error", "name": "alpha",
                                  "instance": "s0", "exception_id": "E"})
        self.rebound_veto("invalid root exception")

    def test_unfinished_or_overlapping_phases_reject(self):
        self.append_before_finish({"type": "phase-start", "phase_id": "unfinished", "nodeid": "node", "when": "call"})
        self.rebound_veto("unfinished phase")
        self.append_before_finish({"type": "phase-start", "phase_id": "other", "nodeid": "node", "when": "call"})
        self.rebound_veto("phase start differs")

    def test_caught_exception_observation_does_not_require_failed_phase(self):
        exc = copy.deepcopy(self.event("exception"))
        exc.pop("id")
        exc.update(exception_id="caught-E", phase_id="caught-phase", cause=None, context=None,
                   suppress_context=False)
        frame = copy.deepcopy(self.event("frame"))
        frame.pop("id")
        frame["exception_id"] = "caught-E"
        self.append_before_finish({"type": "phase-start", "phase_id": "caught-phase", "nodeid": "caught-node", "when": "call"},
                                  exc, frame, {"type": "exception-observed", "exception_id": "caught-E"},
                                  {"type": "phase-end", "phase_id": "caught-phase", "outcome": "passed", "root_exception": None})
        self.check(reference_raw=reference(self.artifact, self.loaders, self.codes))

    def test_job_nonce_policy_catalog_and_reference_pins_are_exact(self):
        for key in ("job_id", "nonce", "runtime_policy_sha256", "catalog_sha256"):
            with self.subTest(key=key):
                self.setUp()
                self.artifact[key] = "other-job" if key == "job_id" else pin("wrong-" + key)
                with self.assertRaisesRegex(origins.OriginError, "^complete independent artifact differs$"):
                    self.check()
        with self.assertRaisesRegex(origins.OriginError, "^reference raw SHA differs$"):
            origins.validate_runtime_origins(encoded(self.artifact), reference_raw=self.reference, reference_sha256=pin("wrong-ref"))

    def test_plain_canonical_json_rejects_duplicate_keys_numbers_and_extra_fields(self):
        for raw, diagnostic in ((b'{"x":1,"x":2}\n', "duplicate JSON key"),
                                (b'{"x":NaN}\n', "noninteger JSON number"),
                                (b'{"x":1.0}\n', "noninteger JSON number"),
                                (encoded(self.artifact)[:-1], "noncanonical artifact bytes"),
                                (encoded(self.artifact) + b"\n", "noncanonical artifact bytes")):
            with self.subTest(diagnostic=diagnostic):
                with self.assertRaisesRegex(origins.OriginError, "^" + diagnostic + "$"):
                    origins.validate_runtime_origins(raw, reference_raw=self.reference,
                                                    reference_sha256=hashlib.sha256(self.reference).hexdigest())
        self.artifact["coverage_complete"] = True
        self.rebound_veto("exact fields differ")

    def test_bytes_depth_and_event_budgets_are_unresolved_not_exclusions(self):
        for raw, diagnostic in ((b" " * (origins.MAX_BYTES + 1), "input byte budget"),
                                (b"[" * (origins.MAX_DEPTH + 1) + b"0" + b"]" * (origins.MAX_DEPTH + 1), "input depth budget")):
            with self.subTest(diagnostic=diagnostic):
                with self.assertRaisesRegex(origins.OriginError, "^" + diagnostic + "$"):
                    origins.validate_runtime_origins(raw, reference_raw=self.reference,
                                                    reference_sha256=hashlib.sha256(self.reference).hexdigest())
        self.artifact["events"] = [{"id": i, "type": "start"} for i in range(1, origins.MAX_EVENTS + 2)]
        self.artifact["events"][-1]["type"] = "finish"
        self.rebound_veto("event budget")

    def test_unicode_ascii_domain_and_old_report_are_not_reinterpreted(self):
        raw = encoded(self.artifact)
        self.assertIn(b"\\u00e9\\u2028", raw)
        self.assertNotIn("é".encode(), raw)
        self.check()
        self.artifact["schema"] = "solcodex.control-phase-report.v5"
        self.rebound_veto("origin schema differs")

    def test_malformed_shapes_and_unaudited_profiles_reject(self):
        for attack in ("event", "profile", "missing-audit"):
            with self.subTest(attack=attack):
                self.setUp()
                if attack == "event":
                    self.artifact["events"][0] = None
                    diagnostic = "malformed origin/reference structure"
                elif attack == "profile":
                    self.artifact["modules"]["instances"]["f0"]["code_profile"] = "unknown"
                    diagnostic = "unknown code profile"
                else:
                    del self.codes["frozen-code"]["audit_sha256"]
                    diagnostic = "exact fields differ"
                self.rebound_veto(diagnostic)
