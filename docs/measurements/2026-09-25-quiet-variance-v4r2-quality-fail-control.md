# v4 revision 2: partial quality-failure campaign control

This is a deterministic local-provider control of the campaign path, not an A/B model experiment or evidence of SoL Codex savings. The candidate protocol still has `model_run_authorized=false` and `campaign_path=null`.

The revised protocol was pushed at `8a54fd2df741c6cadb9eea83a0417bb45cc12c53` before this control started. An intermediate public commit, `fc496bf40a7a77204cb185888a5d6eed847631e6`, changed the CLI adapter and failed the real preflight because it invalidated earlier qualification-control pins. The adapter was restored and two successive real-entrypoint preflights passed before execution. The old four-slot control at `0c65f89cba8ec5ce86a16d3be423efc639b75d1f` remains nonqualifying: its first-slot source was unchanged, so its quality failure did not demonstrate a rejected repair.

The fresh block-1 control ran four separate CLI slots under the revised candidate. Slot 01 recorded a source change to `src/packaging/licenses/__init__.py`, a failed packaging evaluation (6 of 14 behavioral cases passed), and a journaled `continue`. Slots 02–04 passed their evaluations. The journal has four starts and four sealed finishes; slots 05–16 remain unstarted. All four synthetic provider transcripts contain receive, send-intent, and sent events; the pinned provider code calls fsync when writing them. The compact evidence manifest is `b80f172b1d41b12a77ab04c5591bb2d285369d6f67c7d330d5e67223fc6ca40e`; the deterministic archive SHA-256 is `be69c455e5d3b357ab211f7122ad9aa3acf76d93f7ad9978cf2dd551d982d378`.

The independent compact-bundle checker reads the archived journal, seals, host files, provider transcripts, quality stdout, fixture and checkpoint trees, and both SQLite ledgers without importing the coordinator or evaluator. It computes 12 synthetic provider requests and 1,560 input, 780 cached input, and 360 output tokens. Its recorded scenario shape passes; `campaign_path_qualified` remains `false`.

To reproduce that partial decision from this checkout:

```bash
mkdir -p /tmp/solcodex-v4r2-control
tar -xzf docs/measurements/data/2026-09-25-quiet-variance-v4r2-quality-fail-block1-evidence.tar.gz -C /tmp/solcodex-v4r2-control
python3 scripts/verify_quiet_variance_recorded_control_v4.py \
  --bundle /tmp/solcodex-v4r2-control/quality-fail-block1-evidence-v4r2 \
  --plan docs/research/data/2026-09-25-quiet-variance-bundle-v4-campaign-expectations.json \
  --public-root .
PYTHONPATH=scripts PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -q \
  scripts/test_verify_quiet_variance_recorded_control_v4.py
```

The compact archive omits the copied CLI and Code Mode host binaries, Python runtime, CLI home, and live workspace. The checker does not rerun the external behavioral evaluator or independently observe process exits, network delivery, crash boundaries, historical push timing, or the mapping from each synthetic provider request to a ledger attempt. The remaining 18 prospective control cases and independent qualification review are open. No model campaign is authorized, and no general efficiency claim follows from this control.
