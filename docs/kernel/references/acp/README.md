# ACP Reference Mirror

Local snapshot of the [Agent Client Protocol](https://agentclientprotocol.com/)
specification pages and machine-readable schema. Mirrored so kernel design
work can reference ACP exactly without re-fetching.

**Do not edit mirrored files by hand.** If upstream changes, re-fetch from
the authoritative sources below and update this README.

## Source

- **Human docs**: <https://github.com/agentclientprotocol/agent-client-protocol/tree/main/docs>
- **Machine schema**: <https://github.com/agentclientprotocol/agent-client-protocol/blob/main/schema/schema.json>
- **Snapshot date**: 2026-04-29
- **Upstream commit**: `9487d733f5c0a74144e49690eb472b33a723885b`
- **Upstream commit date**: 2026-04-28T18:54:00Z
- **Protocol version at snapshot time**: `1`

## Contents

### `protocol/` — Core protocol pages

| File | Content |
|---|---|
| [overview.md](protocol/overview.md) | Actor model, message flow, JSON-RPC basics |
| [initialization.md](protocol/initialization.md) | `initialize`, capabilities, `authenticate` |
| [session-setup.md](protocol/session-setup.md) | `session/new`, `session/load`, `session/resume`, MCP servers |
| [session-list.md](protocol/session-list.md) | Optional `session/list` |
| [session-modes.md](protocol/session-modes.md) | Optional `session/set_mode` |
| [session-config-options.md](protocol/session-config-options.md) | Optional `session/set_config_option` |
| [prompt-turn.md](protocol/prompt-turn.md) | `session/prompt`, `session/update`, cancellation |
| [tool-calls.md](protocol/tool-calls.md) | Tool call lifecycle and `session/request_permission` |
| [content.md](protocol/content.md) | `ContentBlock` variants |
| [agent-plan.md](protocol/agent-plan.md) | `plan` updates |
| [slash-commands.md](protocol/slash-commands.md) | `available_commands_update` |
| [file-system.md](protocol/file-system.md) | Client-side `fs/*` methods |
| [terminals.md](protocol/terminals.md) | Client-side `terminal/*` methods |
| [transports.md](protocol/transports.md) | Transport guidance |
| [error.md](protocol/error.md) | Error semantics |
| [extensibility.md](protocol/extensibility.md) | `_meta` and underscore-prefixed extension methods |
| [schema.md](protocol/schema.md) | Human-readable type reference |

### `rfds/` — Request For Discussion drafts

All current upstream RFD pages are mirrored under [rfds/](rfds/). Important
ones for Mustang protocol work:

| File | Why Mustang cares |
|---|---|
| [request-cancellation.md](rfds/request-cancellation.md) | `$/cancel_request` per-request cancellation |
| [session-close.md](rfds/session-close.md) | Official session resource release semantics |
| [session-resume.md](rfds/session-resume.md) | Resume without replay |
| [session-delete.md](rfds/session-delete.md) | Possible future official delete semantics |
| [session-usage.md](rfds/session-usage.md) | Token/cost reporting direction |
| [session-info-update.md](rfds/session-info-update.md) | Metadata update semantics |
| [auth-methods.md](rfds/auth-methods.md) | ACP-level authentication evolution |
| [mcp-over-acp.md](rfds/mcp-over-acp.md) | MCP bridging over ACP |
| [v2-prompt.md](rfds/v2-prompt.md) | Future prompt shape changes |

### Machine schema

- [schema.json](schema.json) — full JSON Schema definition. Use this, not
  markdown prose, when writing or validating Pydantic/TypeScript wire models.
- [v2-changes.md](v2-changes.md) — upstream notes for future protocol changes.

## Re-fetching

```bash
cd /home/saki/Documents/truenorth/mustang

for page in overview initialization session-setup session-list \
            session-modes session-config-options prompt-turn tool-calls \
            content extensibility schema file-system terminals transports \
            slash-commands error agent-plan; do
  curl -fsSL -o "docs/kernel/references/acp/protocol/${page}.md" \
    "https://raw.githubusercontent.com/agentclientprotocol/agent-client-protocol/main/docs/protocol/${page}.mdx"
done

for rfd in about acp-agent-registry additional-directories \
           agent-telemetry-export auth-methods boolean-config-option \
           custom-llm-endpoint diff-delete elicitation \
           introduce-rfd-process logout-method mcp-over-acp message-id \
           meta-propagation next-edit-suggestions proxy-chains \
           request-cancellation rust-sdk-v1 session-close \
           session-config-options session-delete session-fork \
           session-info-update session-list session-resume session-usage \
           streamable-http-websocket-transport updates v2-prompt; do
  curl -fsSL -o "docs/kernel/references/acp/rfds/${rfd}.md" \
    "https://raw.githubusercontent.com/agentclientprotocol/agent-client-protocol/main/docs/rfds/${rfd}.mdx"
done

curl -fsSL -o docs/kernel/references/acp/schema.json \
  https://raw.githubusercontent.com/agentclientprotocol/agent-client-protocol/main/schema/schema.json
curl -fsSL -o docs/kernel/references/acp/v2-changes.md \
  https://raw.githubusercontent.com/agentclientprotocol/agent-client-protocol/main/docs/v2-changes.md
```

## Usage in Kernel Design

Kernel design docs should link into this mirror rather than restating ACP
field definitions. When a Mustang behavior is not in [schema.json](schema.json),
it must be documented as either:

- an official RFD adoption, if the method/shape is already proposed upstream; or
- a Mustang extension using ACP's underscore-prefixed extension method rule and
  `mustang.agent/...` `_meta` keys.
