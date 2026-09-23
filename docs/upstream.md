# Upstream attribution

SoL Codex is an independent Codex-specific implementation conceptually inspired by NVIDIA Research's SoL-Pi work:

- [NVlabs/SoL-Pi source repository](https://github.com/NVlabs/SoL-Pi)
- [SoL-Pi paper on arXiv](https://arxiv.org/abs/2609.20519)
- [SoL-Pi project page](https://nvlabs.github.io/SoL-Pi/)
- [SoL-Pi license](https://raw.githubusercontent.com/NVlabs/SoL-Pi/main/LICENSE)

No SoL-Pi source code is copied into this repository. SoL Codex is not a fork, is not endorsed by NVIDIA, and does not transfer SoL-Pi benchmark results to Codex. The names, runtime, integration points, and measurements are different.

The concrete adaptation uses Codex lifecycle hooks to:

- archive eligible local tool output exactly;
- request bounded hook feedback; code-mode scripts may still receive the original result;
- track verification debt after code mutation;
- preserve that reminder across compaction; and
- aggregate byte counts by the model already active in Codex.

It does not implement SoL-Pi's inference runtime, reducer-model protocol, or evaluation harness.

Plugin packaging and hook configuration follow the primary OpenAI documentation:

- [Build plugins](https://developers.openai.com/plugins/build/plugins)
- [Codex hooks](https://learn.chatgpt.com/docs/hooks)

The repository and distributed plugin use the MIT License. SoL-Pi's license is linked above for independent review; compatibility does not imply shared code or endorsement.
