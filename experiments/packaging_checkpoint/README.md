# Complete-checkout packaging checkpoint qualification

This is a secret-free **development** experiment on the already exposed historical [packaging #928](https://github.com/pypa/packaging/issues/928) repair. The [protocol](protocol.json) pins the parent and historical-fix Git trees, Docker image, build and test wheels, external verifier, six scenarios, and expected controls before the Linux run. It makes zero model calls.

The controller checks out the parent source into a disposable workspace. A non-root worker with no network access creates an alternative repair using a new helper module, deletes a checkpoint canary, adds an executable file and an internal symlink, and leaves a forged evaluation result. The controller stops the worker, captures the **whole tree** through anchored, no-follow file operations, and checks the saved manifest. A second fresh worker must produce the same manifest and behavior vector. A symptom-only wrong fix changes candidate tests and leaves a forged success file; neither is used for acceptance. Removing the alternative's helper must break installed imports.

For each source snapshot, a separate network-free container builds a wheel using hash-pinned `flit_core`. A second container installs the controller-verified wheel and records raw observations for the frozen verifier's 14 cases. It receives no candidate source checkout, verifier, expected results, or trusted tests. The controller judges observations against expected values outside that container. Each case imports the installed package in a fresh process; import-time fake output or early exit fails the result protocol. A third container independently installs the wheel and runs the historical-fix commit's `tests/test_metadata.py`. The wrapper checks import provenance and emits fixed-field results. Snapshot and control tests reject escaping or cyclic symlinks, special files, oversized data, and partial captures.

On Linux x86-64 with Docker Engine, run:

```sh
python3 -m unittest experiments.packaging_checkpoint.test_snapshot experiments.packaging_checkpoint.test_run experiments.packaging_checkpoint.test_verify_installed -v
python3 -m experiments.packaging_checkpoint.run --output /tmp/sol-packaging-checkpoint.json
```

The controller downloads only the protocol's public wheels after verifying each SHA-256. Agent, build, and evaluator containers have no network and no credentials. The [CI workflow](../../.github/workflows/packaging-checkpoint-qualification.yml) reruns the same commands and the separate containment preflight. A public CI notice reports only fixed-field aggregate results; the full report is written to the selected output path.

Passing this pilot would qualify a **mechanism** for preserving and externally checking an exposed repair with a material added source file. The 14 behavior cases and one upstream test module are incomplete evidence of package correctness. The alternative is manually authored and the worker is scripted. Candidate Python can still forge valid observations inside its own process; this pilot does not qualify arbitrary malicious code, independent repair quality, the proposed 4-CPU/16-GiB worker, provider billing, or SoL Codex savings.
