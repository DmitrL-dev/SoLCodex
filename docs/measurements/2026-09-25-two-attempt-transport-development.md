# Two-attempt synthetic transport: development result

The [expectation matrix](../research/data/2026-09-25-two-attempt-transport-expectations.json) was pushed in commit `dfa5e5f` before the final normalized probe. The [public result](data/2026-09-25-two-attempt-transport-result.json) matches both cases. This uses a loopback provider and synthetic credential; **no model was called, and no held-out launch is authorized**.

In the positive case, the CLI client disconnected after receiving the first streamed delta. The broker recorded failed delivery to that client, while the host bridge finished streaming to the broker and retained the first provider completion. A second request used a distinct attempt ID and completed. Both upstream attempts were counted once: input `320` (including `190` cached), output `80`, input-plus-output `400`, uncached input `130`. Reconciliation completed after the late first response; the first diagnostic was exact, and the source checkpoint was captured unchanged.

In the negative case, the first provider event lacked final usage. The second attempt contributed a known subtotal of input `200` (including `150` cached) plus output `50`, but **complete-run usage stayed `null`** and reconciliation failed. The exact diagnostic and source checkpoint did not make accounting complete. The synthetic evaluator intentionally returned `fail`; neither case establishes repair acceptance. Provider billing was not independently checked.

The [probe](../../scripts/qualify_two_attempt_transport.py) runs the [published fixture snapshot](fixtures/2026-09-25-test-live-pilot.py) against the private harness and records source hashes. Recompute the public decision and cache arithmetic with:

```sh
python3 scripts/reduce_two_attempt_transport.py
```

The reducer reports `synthetic_transport_probe_pass=true`, `model_run_authorized=false`, positive total `400`, and negative partial subtotal `250`. The fixture's exploratory tests ran while a blind Astra reviewer specified the expected semantics; this is a development check, not prospectively blinded validation. The private transport, runtime and evaluator assets remain unavailable in this repository, so external readers can reproduce the aggregate decision from published records but cannot independently replay the full synthetic run. The failed exposed four-run model screen remains failed.
