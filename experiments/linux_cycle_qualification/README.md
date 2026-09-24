# Linux worker lifecycle qualification

This secret-free **development** check joins a scripted worker, an independent mock upstream journal, a broker using the existing attempt ledger, a saved workspace checkpoint, and an external behavior evaluator. The [protocol](protocol.json) lists ten scenarios before the CI run. There are zero model calls and no real provider billing data.

The worker runs in a non-root, read-only Docker container with an isolated internal network and only two bind mounts: its writable workspace and a read-only script. The broker also joins an egress bridge to reach the mock upstream on the host. The upstream writes an accepted request to its own SQLite WAL journal before releasing any completion and never mounts that journal into worker or broker. The controller observes that durable record before it kills a worker or broker. A broker restart is represented by opening the saved attempt ledger in a separate reconciliation process; the original `pending` or `unknown` state remains intact, and the mock record is attached separately.

The scenarios cover normal completion, worker death after acceptance, broker death after acceptance, timeout after a source edit, two distinct retry attempts, repeated completion, conflicting completion, accepted request without final usage, an unavailable reconciliation record, and failure to write the local ledger before dispatch. The controller evaluates the exact timeout checkpoint through isolated Docker invocations of the published 34-case action-fusion verifier. It also checks the parent, the published gold source, and each wrong-fix witness. A forged evaluation file inside the worker workspace says `accepted=false` while the source passes; the external result must come from executing the source. These are mechanism checks on an exposed fixture, not independent repair quality evidence.

Run on Linux x86-64 with Docker Engine supporting isolated gateway mode:

```sh
python3 -m unittest experiments.linux_cycle_qualification.test_reconcile experiments.linux_cycle_qualification.test_mock_upstream experiments.linux_cycle_qualification.test_mock_broker -v
python3 -m experiments.linux_cycle_qualification.run --output /tmp/sol-linux-cycle.json
```

The [workflow](../../.github/workflows/linux-cycle-qualification.yml) uses the same pinned Python image as the public action-fusion replay. A passing mock journal only qualifies this simulated failure path. It cannot recover usage from a real provider without a separately accessible request record, and the current ChatGPT-authenticated route has no verified request-to-bill reconciliation source. The 2-CPU/2-GiB worker used here does not qualify the proposed 4-CPU/16-GiB, 60-minute confirmation worker. Held-out task selection and acceptance adjudication remain separate gates.
