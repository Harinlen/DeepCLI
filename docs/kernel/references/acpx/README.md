# ACPX Reference Mirror

Local snapshot of [OpenClaw ACPX](https://github.com/openclaw/acpx), the
headless ACP client/runtime used to drive coding agents such as Codex, Claude,
Gemini, OpenCode, and OpenClaw over the Agent Client Protocol.

**Do not edit mirrored files by hand.** If upstream changes, re-fetch from
the authoritative source below and update this README.

## Source

- **Repository**: <https://github.com/openclaw/acpx>
- **Snapshot date**: 2026-04-29
- **Upstream commit**: `e1a3546669b93b836b3bad51b2bfd7e41cfbee73`
- **Upstream commit date**: 2026-04-27T10:55:10Z

## Contents

| Path | Content |
|---|---|
| [upstream-README.md](upstream-README.md) | Upstream product README |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [VISION.md](VISION.md) | Project vision |
| [docs/CLI.md](docs/CLI.md) | Command grammar, options, sessions, output modes |
| [docs/2026-02-19-acp-coverage-roadmap.md](docs/2026-02-19-acp-coverage-roadmap.md) | ACP method coverage |
| [docs/2026-02-17-session-management.md](docs/2026-02-17-session-management.md) | Session lookup, queue owner, lifecycle |
| [docs/2026-02-25-warm-session-owner-architecture.md](docs/2026-02-25-warm-session-owner-architecture.md) | Queue owner and warm process architecture |
| [docs/2026-02-27-acpx-session-model.md](docs/2026-02-27-acpx-session-model.md) | Authoritative ACP transcript stream model |
| [docs/2026-03-25-acpx-flows-architecture.md](docs/2026-03-25-acpx-flows-architecture.md) | Flow runtime architecture |
| [docs/2026-03-28-acpx-flow-permission-requirements.md](docs/2026-03-28-acpx-flow-permission-requirements.md) | Flow permission requirements |
| [agents/README.md](agents/README.md) | Built-in agent profile index |
| [examples/](examples/) | Flow examples |

## Mustang-Relevant Takeaways

- ACPX is not a replacement protocol for ACP. It is a headless ACP
  client/runtime that manages external ACP-compatible coding agents.
- ACPX overlaps with Mustang's client/runtime glue: persistent sessions,
  named sessions, prompt queueing, cooperative cancel, soft-close lifecycle,
  permission modes, local status/history, process ownership, and strict ACP
  transcript persistence.
- ACPX supports client-authority `fs/*`, `terminal/*`, and `authenticate`
  paths that Mustang currently avoids for the kernel-owned local tool model.
- The most likely Mustang adoption shape is an external Session Agent backend:
  Mustang keeps kernel-owned memory/tools/hooks/session truth, while ACPX runs
  and coordinates external coding-agent processes when the user asks for a
  Codex/Claude/Gemini/OpenCode-style session agent.

## Re-fetching

```bash
cd /home/saki/Documents/truenorth/mustang

curl -fsSL -o docs/kernel/references/acpx/upstream-README.md \
  https://raw.githubusercontent.com/openclaw/acpx/main/README.md
curl -fsSL -o docs/kernel/references/acpx/CHANGELOG.md \
  https://raw.githubusercontent.com/openclaw/acpx/main/CHANGELOG.md
curl -fsSL -o docs/kernel/references/acpx/VISION.md \
  https://raw.githubusercontent.com/openclaw/acpx/main/VISION.md

for doc in 2026-02-17-agent-registry 2026-02-17-architecture \
           2026-02-17-session-management 2026-02-19-acp-coverage-roadmap \
           2026-02-19-mock-agent-testing 2026-02-22-openclaw-integration-plan \
           2026-02-23-session-identity-spec \
           2026-02-25-warm-session-owner-architecture \
           2026-02-27-acpx-session-model 2026-02-27-zed-thread-schema \
           2026-03-25-acpx-flows-architecture \
           2026-03-26-acpx-flow-trace-replay \
           2026-03-27-flow-replay-viewer \
           2026-03-28-acpx-flow-permission-requirements \
           2026-03-31-flow-replay-live-transport \
           2026-04-06-built-in-agent-launch-ownership \
           ACPX_ERROR_STRATEGY CLI json-patch-plus; do
  curl -fsSL -o "docs/kernel/references/acpx/docs/${doc}.md" \
    "https://raw.githubusercontent.com/openclaw/acpx/main/docs/${doc}.md"
done
```
