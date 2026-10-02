"""PROSPECTIVE DEVELOPMENT: typed origin ARTIFACT consistency, not observation.

No repository imports, filesystem access, compiler, launcher or producer here.
The caller supplies independently protected reference bytes AND their external
SHA. Copying the observed artifact into that reference proves nothing authentic.
All limits/unsupported evidence mean unresolved original-denominator evidence.
This component cannot qualify controls, candidates, old V5 reports or close I2.

Prospective serialization contract: exception records declare graph nodes.
exception-observed/import-error markers require an ALREADY declared exception
in the current phase (or null scope outside phases). Forward cause/context/child
edges are allowed and resolved after replay. Declaration/marker ordering is a
serialization dependency, NOT proof or a timestamp of native exception raising.
Retained old streams are never reordered or converted by this validator.
"""

import hashlib
import json

SCHEMA = "solcodex.runtime-origin-evidence.v1"
REFERENCE_SCHEMA = "solcodex.runtime-origin-reference.v1"
RESULT_SCHEMA = "solcodex.runtime-origin-consistency.v1"
FINGERPRINT_SCHEMA = "solcodex.code-object-structure.v1"
CANONICAL_DOMAIN = "json-sort-compact-ascii-no-nan.v1"
MAX_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 48
MAX_VALUES = 250000
MAX_EVENTS = 20000
MAX_INSTANCES = 10000
KINDS = {"source", "native", "builtin", "frozen", "direct-script-main"}
EVENT_FIELDS = {
    "start": "", "finish": "",
    "create": "instance", "retire": "instance",
    "import": "name instance outcome",
    "import-error": "name instance exception_id",
    "unload": "name instance", "reload": "name old_instance instance",
    "replace": "name old_instance instance",
    "alias-set": "name instance", "alias-remove": "name instance",
    "alias-rebind": "name old_instance instance",
    "negative-cache-set": "name", "negative-cache-resolve": "name",
    "phase-start": "phase_id nodeid when",
    "phase-end": "phase_id outcome root_exception",
    "exception-observed": "exception_id",
    "exception": "exception_id phase_id classes message traceback_state cause context children suppress_context",
    "frame": "exception_id instance observed_module binding_event_id fingerprint_schema fingerprint_sha256 filename function line",
}


class OriginError(ValueError):
    """Missing/unsupported/inconsistent evidence; never exclusion or score."""


def require(condition, message):
    if not condition:
        raise OriginError(message)


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()), "exact fields differ")


def text(value):
    require(type(value) is str and 0 < len(value) <= 4096 and "\0" not in value, "invalid identifier")
    return value


def sha(value):
    require(type(value) is str and len(value) == 64
            and all(c in "0123456789abcdef" for c in value), "invalid SHA256")
    return value


def integer(value, minimum=1):
    require(type(value) is int and minimum <= value < 2**63, "invalid integer")
    return value


def path(value):
    text(value)
    require(value.startswith("/") and all(p not in ("", ".", "..") for p in value[1:].split("/")),
            "noncanonical absolute origin")


def sequence(value):
    require(type(value) is list, "list required")
    return value


def mapping(value):
    require(type(value) is dict, "mapping required")
    return value


def canonical(value):
    # Public utility for plain JSON fixtures; validate_runtime_origins accepts
    # bytes only, never a caller object with custom iteration/serialization hooks.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _number(raw):
    require(len(raw) <= 20, "integer token budget")
    value = int(raw)
    require(-(2**63) < value < 2**63, "integer range")
    return value


def _not_number(raw):
    raise OriginError("noninteger JSON number")


def _decode(raw):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, "input byte budget")
    # Bound nesting BEFORE recursive JSON decoding, respecting escaped quotes.
    depth, quoted, escaped = 0, False, False
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            require(depth <= MAX_DEPTH, "input depth budget")
        elif byte in (93, 125):
            depth -= 1
            require(depth >= 0, "invalid JSON nesting")
    require(depth == 0 and not quoted, "invalid JSON nesting")
    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs,
                           parse_int=_number, parse_float=_not_number, parse_constant=_not_number)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise OriginError("invalid ASCII canonical JSON") from error
    pending, count = [value], 0
    while pending:
        item = pending.pop()
        count += 1
        require(count <= MAX_VALUES, "input value budget")
        if type(item) is dict:
            pending.extend(item.keys())
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
        else:
            require(type(item) in (str, int, bool) or item is None, "nonplain JSON value")
    require(raw == canonical(value) + b"\n", "noncanonical artifact bytes")
    return value


def _profiles(loaders, codes):
    for name, entry in mapping(loaders).items():
        text(name)
        fields(entry, "kind image_sha256 loader_sha256 audit_sha256")
        require(type(entry["kind"]) is str and entry["kind"] in KINDS, "unknown loader kind")
        for key in ("image_sha256", "loader_sha256", "audit_sha256"):
            sha(entry[key])
    for name, entry in mapping(codes).items():
        text(name)
        fields(entry, "kind image_sha256 source_sha256 audit_sha256 fingerprint_schema codes")
        require(entry["kind"] in ("source", "code-image"), "unknown code profile")
        sha(entry["image_sha256"])
        sha(entry["audit_sha256"])
        require(entry["fingerprint_schema"] == FINGERPRINT_SCHEMA, "fingerprint schema differs")
        if entry["kind"] == "source":
            sha(entry["source_sha256"])
        else:
            require(entry["source_sha256"] is None, "code image is not source")
        for fingerprint, code in mapping(entry["codes"]).items():
            sha(fingerprint)
            fields(code, "filename function lines")
            text(code["filename"])
            text(code["function"])
            lines = sequence(code["lines"])
            require(bool(lines), "missing code locations")
            for line in lines:
                integer(line)
            require(lines == sorted(set(lines)), "code lines not unique ordered")


def _module(record, loaders, codes):
    fields(record, "name object_id generation kind origin source_sha256 image_sha256 loader_profile observed_file spec_name spec_origin code_profile")
    text(record["name"])
    text(record["object_id"])
    integer(record["generation"], 0)
    kind = record["kind"]
    require(type(kind) is str and kind in KINDS, "unknown module kind")
    image = sha(record["image_sha256"])
    profile = text(record["loader_profile"])
    require(profile in loaders and loaders[profile]["kind"] == kind
            and loaders[profile]["image_sha256"] == image, "loader/image profile differs")
    require(record["observed_file"] is None or type(record["observed_file"]) is str,
            "observed_file must preserve literal string or null")
    if kind in ("builtin", "frozen"):
        require(record["origin"] == ("built-in" if kind == "builtin" else "frozen")
                and record["source_sha256"] is None, "embedded origin/source differs")
    else:
        path(record["origin"])
        sha(record["source_sha256"])
    if kind == "direct-script-main":
        require(record["name"] == "__main__" and record["spec_name"] is None
                and record["spec_origin"] is None, "direct script must retain None spec")
    else:
        require(record["spec_name"] == record["name"] and record["spec_origin"] == record["origin"],
                "canonical spec identity differs")
    if record["code_profile"] is not None:
        profile = text(record["code_profile"])
        require(profile in codes, "unknown code profile")
        code = codes[profile]
        require(code["image_sha256"] == image, "code image binding differs")
        if kind in ("source", "direct-script-main"):
            require(code["kind"] == "source" and code["source_sha256"] == record["source_sha256"],
                    "code source binding differs")
        else:
            require(code["kind"] == "code-image", "sourceless frame needs audited code image")


def _artifact(value, loaders, codes):
    fields(value, "schema job_id nonce runtime_policy_sha256 catalog_sha256 modules aliases events")
    require(value["schema"] == SCHEMA, "origin schema differs")
    text(value["job_id"])
    for key in ("nonce", "runtime_policy_sha256", "catalog_sha256"):
        sha(value[key])
    inventory = value["modules"]
    fields(inventory, "instances final live_instances negative_cache")
    records = mapping(inventory["instances"])
    require(len(records) <= MAX_INSTANCES, "instance budget")
    for instance, record in records.items():
        text(instance)
        _module(record, loaders, codes)
    for name, instance in mapping(inventory["final"]).items():
        text(name)
        text(instance)
        require(instance in records and records[instance]["name"] == name, "final canonical identity differs")
    for key in ("live_instances", "negative_cache"):
        entries = sequence(inventory[key])
        for entry in entries:
            text(entry)
        require(entries == sorted(set(entries)), "final set must be unique ordered")
    for name, target in mapping(value["aliases"]).items():
        text(name)
        text(target)
        require(name not in inventory["final"] and target in inventory["final"], "alias chain or absent canonical target")
    events = sequence(value["events"])
    require(2 <= len(events) <= MAX_EVENTS, "event budget")
    require(events[0].get("type") == "start" and events[-1].get("type") == "finish", "missing stream boundaries")
    live, seen, objects, loaded, aliases, negative = set(), set(), set(), {}, {}, set()
    bindings, exceptions, frames, phases, roots = {}, {}, {}, {}, []
    active = None
    witness_values = 0

    def instance_record(instance):
        text(instance)
        require(instance in records, "unknown instance")
        return records[instance]

    def remember(event_id, names, instance):
        nonlocal witness_values
        witness_values += len(names)
        require(witness_values <= MAX_VALUES, "binding witness budget")
        bindings[event_id] = (frozenset(names), instance)

    for ordinal, event in enumerate(events, 1):
        require(type(event) is dict and type(event.get("type")) is str
                and event["type"] in EVENT_FIELDS, "unknown or unresolved event")
        kind = event["type"]
        fields(event, "id type " + EVENT_FIELDS[kind])
        require(integer(event["id"]) == ordinal, "event IDs skipped reordered duplicated")
        if "name" in event:
            text(event["name"])
        if kind in ("start", "finish"):
            require(ordinal == (1 if kind == "start" else len(events)), "misplaced stream boundary")
            continue
        if kind == "create":
            instance = event["instance"]
            record = instance_record(instance)
            require(instance not in seen and record["object_id"] not in objects
                    and record["generation"] == 0, "instance/object reused")
            seen.add(instance)
            live.add(instance)
            objects.add(record["object_id"])
        elif kind == "retire":
            instance = event["instance"]
            instance_record(instance)
            require(instance in live and instance not in loaded.values() and instance not in aliases.values(),
                    "retire bound or nonlive instance")
            live.remove(instance)
        elif kind == "import":
            name, instance, outcome = event["name"], event["instance"], event["outcome"]
            require(outcome in ("bound", "missing", "negative"), "unresolved import")
            if outcome == "bound":
                record = instance_record(instance)
                require(instance in live and name not in negative, "import nonlive or negative instance")
                if name in aliases:
                    require(aliases[name] == instance, "alias import identity differs")
                else:
                    require(record["name"] == name and loaded.get(name, instance) == instance,
                            "import replaces canonical instance without unload/reload")
                    loaded[name] = instance
                remember(ordinal, [name], instance)
            else:
                require(instance is None and name not in loaded and name not in aliases
                        and ((name in negative) == (outcome == "negative")), "missing/negative import differs")
        elif kind == "unload":
            name, instance = event["name"], event["instance"]
            instance_record(instance)
            require(loaded.get(name) == instance, "unload identity differs")
            del loaded[name]  # Object/code may survive in aliases and traceback references.
        elif kind == "reload":
            name, old, new = event["name"], event["old_instance"], event["instance"]
            before, after = instance_record(old), instance_record(new)
            require(loaded.get(name) == old and old in live and new not in seen
                    and after["name"] == name and after["object_id"] == before["object_id"]
                    and after["generation"] == before["generation"] + 1, "reload generation differs")
            live.remove(old)
            live.add(new)
            seen.add(new)
            loaded[name] = new
            names = [name]
            for alias, instance in aliases.items():
                if instance == old:
                    aliases[alias] = new
                    names.append(alias)
            remember(ordinal, names, new)
        elif kind == "replace":
            name, old, new = event["name"], event["old_instance"], event["instance"]
            before, after = instance_record(old), instance_record(new)
            require(loaded.get(name) == old and new in live and old != new
                    and after["name"] == name and after["object_id"] != before["object_id"],
                    "replacement identity differs")
            loaded[name] = new  # Existing aliases still identify the OLD object.
            remember(ordinal, [name], new)
        elif kind == "alias-set":
            name, instance = event["name"], event["instance"]
            record = instance_record(instance)
            require(instance in live and loaded.get(record["name"]) == instance
                    and name not in loaded and name not in aliases and name not in negative, "invalid alias binding")
            aliases[name] = instance
            remember(ordinal, [name], instance)
        elif kind == "alias-rebind":
            name, old, new = event["name"], event["old_instance"], event["instance"]
            instance_record(old)
            record = instance_record(new)
            require(aliases.get(name) == old and old != new and new in live
                    and loaded.get(record["name"]) == new, "alias rebind identity differs")
            aliases[name] = new
            remember(ordinal, [name], new)
        elif kind == "alias-remove":
            name, instance = event["name"], event["instance"]
            instance_record(instance)
            require(aliases.get(name) == instance, "alias removal identity differs")
            del aliases[name]
        elif kind == "negative-cache-set":
            name = event["name"]
            require(name not in loaded and name not in aliases and name not in negative, "negative cache collision")
            negative.add(name)
        elif kind == "negative-cache-resolve":
            require(event["name"] in negative, "negative cache resolution without entry")
            negative.remove(event["name"])  # None -> absent; later create/import/alias is explicit.
        elif kind == "phase-start":
            phase = text(event["phase_id"])
            text(event["nodeid"])
            require(active is None and phase not in phases and event["when"] in ("setup", "call", "teardown"),
                    "phase start differs")
            phases[phase] = event
            active = phase
        elif kind == "phase-end":
            require(active is not None and event["phase_id"] == active, "phase end differs")
            require(event["outcome"] in ("passed", "failed"), "uncertain phase outcome")
            if event["outcome"] == "passed":
                require(event["root_exception"] is None, "passed phase has root failure")
            else:
                roots.append((text(event["root_exception"]), active))
            active = None
        elif kind == "exception":
            exc = text(event["exception_id"])
            require(exc not in exceptions and event["phase_id"] == active, "exception identity/scope differs")
            require(type(event["message"]) is str and type(event["suppress_context"]) is bool,
                    "exception message/context representation differs")
            require(event["traceback_state"] in ("present", "none"), "unknown traceback state")
            classes = sequence(event["classes"])
            require(bool(classes), "empty exception MRO")
            names = []
            for entry in classes:
                fields(entry, "name instance")
                name = text(entry["name"])
                record = instance_record(entry["instance"])
                require(entry["instance"] in seen and name.startswith(record["name"] + "."),
                        "exception class origin differs")
                names.append(name)
            require(len(names) == len(set(names))
                    and names[-2:] == ["builtins.BaseException", "builtins.object"], "exception MRO differs")
            for target in [event["cause"], event["context"], *sequence(event["children"])]:
                if target is not None:
                    text(target)
            require(None not in event["children"] and len(event["children"]) == len(set(event["children"])),
                    "exception child multiplicity differs")
            exceptions[exc] = event
            frames[exc] = []
        elif kind in ("import-error", "exception-observed"):
            if kind == "import-error":
                instance = event["instance"]
                if instance is not None:
                    record = instance_record(instance)
                    require(instance in seen and record["name"] == event["name"], "import error instance differs")
            exc = text(event["exception_id"])
            # Unlike graph edges, a root marker depends on a prior declaration.
            # Keep null as the explicit scope outside an active pytest phase.
            require(exc in exceptions and exceptions[exc]["phase_id"] == active,
                    "invalid root exception")
            roots.append((exc, active))
        elif kind == "frame":
            exc, instance = text(event["exception_id"]), event["instance"]
            record = instance_record(instance)
            require(exc in exceptions and exceptions[exc]["phase_id"] == active and instance in seen,
                    "frame exception/instance scope differs")
            witness = integer(event["binding_event_id"])
            name = text(event["observed_module"])
            require(witness in bindings and name in bindings[witness][0] and bindings[witness][1] == instance,
                    "frame historical binding differs")
            require(record["code_profile"] is not None, "frame lacks audited code profile")
            profile = codes[record["code_profile"]]
            fingerprint = sha(event["fingerprint_sha256"])
            require(event["fingerprint_schema"] == FINGERPRINT_SCHEMA and fingerprint in profile["codes"],
                    "frame fingerprint differs")
            code = profile["codes"][fingerprint]
            require(type(event["filename"]) is str and type(event["function"]) is str
                    and event["filename"] == code["filename"] and event["function"] == code["function"]
                    and integer(event["line"]) in code["lines"], "frame code locations differ")
            frames[exc].append(ordinal)
    require(active is None, "unfinished phase")
    require(seen == set(records), "unobserved declared instance")
    require(loaded == inventory["final"] and sorted(live) == inventory["live_instances"]
            and sorted(negative) == inventory["negative_cache"], "final module/cache state differs")
    for name, instance in aliases.items():
        require(loaded.get(records[instance]["name"]) == instance, "final alias canonical object absent")
    require({name: records[instance]["name"] for name, instance in aliases.items()} == value["aliases"],
            "final alias state differs")
    # Iterative DFS: exception depth is bounded by input/events, never Python's
    # recursion limit. Graphs preserve suppressed context and shared children.
    colors, reachable = {}, set()
    for root, scope in roots:
        require(root in exceptions and exceptions[root]["phase_id"] == scope
                and exceptions[root]["traceback_state"] == "present", "invalid root exception")
        stack = [(root, False)]
        while stack:
            exc, leaving = stack.pop()
            require(exc in exceptions and exceptions[exc]["phase_id"] == scope, "unresolved exception edge")
            if leaving:
                colors[exc] = 2
                continue
            require(colors.get(exc) != 1, "cyclic exception graph")
            if colors.get(exc) == 2:
                continue
            colors[exc] = 1
            reachable.add(exc)
            stack.append((exc, True))
            event = exceptions[exc]
            for target in [event["cause"], event["context"], *event["children"]]:
                if target is not None:
                    stack.append((target, False))
    require(reachable == set(exceptions), "unreferenced exception event")
    for exc, event in exceptions.items():
        require(bool(frames[exc]) == (event["traceback_state"] == "present"), "traceback state/frame mismatch")


def validate_runtime_origins(raw, *, reference_raw, reference_sha256):
    """Return consistency receipt; external reference truth/custody stays OPEN.

    Both inputs are bounded ASCII-canonical JSON + literal LF bytes. Reference
    exact fields: schema artifact_sha256 artifact loader_profiles code_profiles.
    Its artifact is the FULL independently expected stream and final state, not
    counts, an allowlist or selected frames. Never construct it from raw output.
    """
    sha(reference_sha256)
    reference = _decode(reference_raw)
    require(digest(reference_raw) == reference_sha256, "reference raw SHA differs")
    fields(reference, "schema artifact_sha256 artifact loader_profiles code_profiles")
    require(reference["schema"] == REFERENCE_SCHEMA, "reference schema differs")
    sha(reference["artifact_sha256"])
    expected = canonical(reference["artifact"]) + b"\n"
    require(len(expected) <= MAX_BYTES and digest(expected) == reference["artifact_sha256"],
            "reference artifact SHA differs")
    value = _decode(raw)
    try:
        _profiles(reference["loader_profiles"], reference["code_profiles"])
        _artifact(value, reference["loader_profiles"], reference["code_profiles"])
    except (TypeError, KeyError, AttributeError, IndexError, RecursionError) as error:
        raise OriginError("malformed origin/reference structure") from error
    require(raw == expected, "complete independent artifact differs")
    return {"schema": RESULT_SCHEMA, "status": "structurally-consistent-only",
            "artifact_sha256": digest(raw), "reference_sha256": reference_sha256,
            "job_id": value["job_id"], "nonce": value["nonce"]}
