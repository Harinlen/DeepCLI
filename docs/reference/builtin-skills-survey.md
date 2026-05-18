# Built-in Skills Survey

> Snapshot date: 2026-05-14.
> Scope: local reference check of Claude Code, Codex, OpenClaw, and Hermes
> Agent built-in / bundled skills.  This is a survey document, not an
> implementation plan.

## Source Paths

| System | Local source inspected |
|---|---|
| Claude Code | `/home/saki/Documents/projects/claude-code-main/src/skills/` |
| Codex | `/home/saki/.codex/skills/.system/` |
| OpenClaw | `/home/saki/Documents/alex/openclaw/skills/`, `/home/saki/Documents/alex/openclaw/extensions/*/skills/` |
| Hermes Agent | `/home/saki/Documents/alex/hermes-agent/skills/`, `/home/saki/Documents/alex/hermes-agent/optional-skills/` |

## Shape Comparison

| System | Built-in skill shape | Count observed | Main character |
|---|---:|---:|---|
| Claude Code | TypeScript-registered bundled skills plus file-based `SKILL.md` loading | 14 always/conditionally registered bundled skills observed | Small command-like core: verify/debug/config/memory/browser/scheduling |
| Codex | System `SKILL.md` directories under `$CODEX_HOME/skills/.system` | 5 | Meta-capabilities for image generation, OpenAI docs, plugin/skill creation and installation |
| OpenClaw | Repository skill directories, plus extension-provided skills | 51 core + 8 extension skills | Local CLI and service-control catalog: messaging, Apple/macOS, media, GitHub, MCP, smart home |
| Hermes Agent | Category-organized skill directories, plus optional skill packs | 89 core + 62 optional skills | Broad skill library: coding agents, creative, MLOps, productivity, research, GitHub, app integrations |

## Claude Code Bundled Skills

Claude Code registers bundled skills programmatically from
`src/skills/bundled/index.ts`.  The registry supports descriptions, aliases,
tool allowlists, model/context hints, hidden/user-invocable flags, hooks, and
lazy extraction of bundled reference files.

| Skill | Purpose |
|---|---|
| `batch` | Research and execute large mechanical changes in parallel across isolated worktree agents. |
| `claude-api` | Build applications with the Claude API or Anthropic SDK. |
| `claude-in-chrome` | Automate Chrome tabs: click, fill forms, screenshots, console logs, navigation. |
| `debug` | Enable/read Claude Code debug logs and diagnose current-session issues. |
| `keybindings-help` | Customize `~/.claude/keybindings.json` and chord/key bindings. |
| `loop` | Run a prompt or slash command repeatedly on an interval. |
| `lorem-ipsum` | Generate large filler text for long-context testing. |
| `remember` | Review auto-memory entries and promote/clean them across memory layers. |
| `schedule` | Create, update, list, or run scheduled remote agents/triggers. |
| `simplify` | Review changed code for reuse, quality, and efficiency, then fix findings. |
| `skillify` | Capture the current session's repeatable process into a reusable skill. |
| `stuck` | Internal diagnostic skill for frozen, slow, or stuck Claude Code sessions. |
| `update-config` | Modify Claude Code settings, permissions, hooks, and environment config. |
| `verify` | Verify a code change by running the app and checking real behavior. |

Notes:

- Some skills are gated by feature flags or internal `USER_TYPE` checks.
- `registerBundledSkill()` normalizes bundled skills into the same command
  shape as file-based skills.
- Bundled skills can ship extra files and lazily extract them to a temporary
  skill root, then prefix the prompt with that base directory.

## Codex System Skills

Codex system skills are normal `SKILL.md` directories under
`$CODEX_HOME/skills/.system`.  These are meta-skills that teach Codex how to
use specialized workflows or helper scripts.

| Skill | Purpose |
|---|---|
| `imagegen` | Generate or edit raster images; prefers the built-in `image_gen` tool and uses CLI fallback only for explicit/API-specific cases. |
| `openai-docs` | Answer OpenAI product/API questions from current official docs; includes model selection and migration guidance. |
| `plugin-creator` | Scaffold Codex plugin directories, manifests, optional folders, and marketplace entries. |
| `skill-creator` | Create or update Codex skills with effective frontmatter, instructions, scripts, references, and assets. |
| `skill-installer` | List/install curated or GitHub-hosted Codex skills into `$CODEX_HOME/skills`. |

## OpenClaw Skills

OpenClaw's built-ins are mostly direct operational adapters around local CLIs,
platform integrations, and service-specific workflows.  Core skills are flat
under `skills/`; extension skills are contributed by extension packages.

### Core Skills

| Skill | Purpose |
|---|---|
| `1password` | 1Password CLI setup, sign-in, and secret injection. |
| `apple-notes` | Manage Apple Notes through `memo`. |
| `apple-reminders` | Manage Apple Reminders through `remindctl`. |
| `bear-notes` | Create/search/manage Bear notes through `grizzly`. |
| `blogwatcher` | Monitor RSS/Atom/blog updates. |
| `blucli` | Control BluOS devices. |
| `bluebubbles` | Send/manage iMessages through BlueBubbles. |
| `camsnap` | Capture frames/clips from RTSP/ONVIF cameras. |
| `canvas` | Canvas integration skill. |
| `clawhub` | Search/install/update/publish skills through ClawHub. |
| `coding-agent` | Delegate coding work to Codex, Claude Code, Pi agents, or ACP runtimes. |
| `discord` | Discord operations through the message tool. |
| `eightctl` | Control Eight Sleep pods. |
| `gemini` | Gemini CLI one-shot Q&A, summaries, and generation. |
| `gh-issues` | Fetch GitHub issues, spawn agents, open PRs, and monitor reviews. |
| `gifgrep` | Search/download GIFs and extract stills/sheets. |
| `github` | GitHub operations through `gh` CLI. |
| `gog` | Google Workspace CLI for Gmail, Calendar, Drive, Docs, Sheets, Contacts. |
| `goplaces` | Google Places lookup through `goplaces`. |
| `healthcheck` | Host security hardening and deployment posture checks. |
| `himalaya` | IMAP/SMTP email through Himalaya CLI. |
| `imsg` | iMessage/SMS through Messages.app. |
| `mcporter` | Configure, authenticate, and call MCP servers/tools directly. |
| `model-usage` | Summarize Codex/Claude model usage from CodexBar cost data. |
| `nano-pdf` | Edit PDFs with natural-language instructions. |
| `node-connect` | Diagnose OpenClaw node pairing and companion-app connection failures. |
| `notion` | Notion pages, databases, and blocks. |
| `obsidian` | Work with Obsidian vaults and `obsidian-cli`. |
| `openai-whisper` | Local speech-to-text through Whisper CLI. |
| `openai-whisper-api` | Audio transcription through OpenAI Audio Transcriptions API. |
| `openhue` | Philips Hue lights and scenes. |
| `oracle` | Oracle CLI best practices for prompt/file/session usage. |
| `ordercli` | Foodora order history and active order status. |
| `peekaboo` | Capture and automate macOS UI. |
| `sag` | ElevenLabs TTS with `say`-style UX. |
| `session-logs` | Search/analyze previous session logs. |
| `sherpa-onnx-tts` | Offline local TTS. |
| `skill-creator` | Create, improve, audit, and package AgentSkills. |
| `slack` | Slack message, reaction, pin, and channel/DM operations. |
| `songsee` | Audio spectrogram and feature-panel visualization. |
| `sonoscli` | Sonos discovery, status, playback, volume, grouping. |
| `spotify-player` | Terminal Spotify playback/search. |
| `summarize` | Summarize/extract text or transcripts from URLs and files. |
| `things-mac` | Manage Things 3 tasks/projects through CLI and URL scheme. |
| `tmux` | Remote-control tmux sessions by sending keys and reading pane output. |
| `trello` | Trello board/list/card management. |
| `video-frames` | Extract frames/clips from video through ffmpeg. |
| `voice-call` | Start voice calls through the OpenClaw voice-call plugin. |
| `wacli` | WhatsApp messaging/search/sync through `wacli`. |
| `weather` | Current weather and forecasts through wttr.in or Open-Meteo. |
| `xurl` | X/Twitter API operations through `xurl`. |

### Extension Skills

| Skill | Extension | Purpose |
|---|---|---|
| `acp-router` | `acpx` | Route plain-language requests into ACP runtime sessions or `acpx` sessions. |
| `diffs` | `diffs` | Produce shareable real diffs instead of manual summaries. |
| `feishu-doc` | `feishu` | Feishu document read/write operations. |
| `feishu-drive` | `feishu` | Feishu cloud storage file management. |
| `feishu-perm` | `feishu` | Feishu document/file permission management. |
| `feishu-wiki` | `feishu` | Feishu knowledge-base/wiki navigation. |
| `prose` | `open-prose` | OpenProse VM skill pack for multi-agent workflows. |
| `tavily` | `tavily` | Tavily search, content extraction, and research tools. |

## Hermes Agent Skills

Hermes keeps a much larger built-in library organized by domain.  Core skills
live under `skills/`; optional installable packs live under `optional-skills/`.

### Core Category Inventory

| Category | Count | Skills |
|---|---:|---|
| `apple` | 4 | `apple-notes`, `apple-reminders`, `findmy`, `imessage` |
| `autonomous-ai-agents` | 4 | `claude-code`, `codex`, `hermes-agent`, `opencode` |
| `creative` | 19 | `architecture-diagram`, `ascii-art`, `ascii-video`, `baoyu-comic`, `baoyu-infographic`, `claude-design`, `comfyui`, `ideation`, `design-md`, `excalidraw`, `humanizer`, `manim-video`, `p5js`, `pixel-art`, `popular-web-designs`, `pretext`, `sketch`, `songwriting-and-ai-music`, `touchdesigner-mcp` |
| `data-science` | 1 | `jupyter-live-kernel` |
| `devops` | 3 | `kanban-orchestrator`, `kanban-worker`, `webhook-subscriptions` |
| `dogfood` | 1 | `dogfood` |
| `email` | 1 | `himalaya` |
| `gaming` | 2 | `minecraft-modpack-server`, `pokemon-player` |
| `github` | 6 | `codebase-inspection`, `github-auth`, `github-code-review`, `github-issues`, `github-pr-workflow`, `github-repo-management` |
| `mcp` | 1 | `native-mcp` |
| `media` | 5 | `gif-search`, `heartmula`, `songsee`, `spotify`, `youtube-content` |
| `mlops` | 13 | `evaluating-llms-harness`, `weights-and-biases`, `huggingface-hub`, `llama-cpp`, `obliteratus`, `outlines`, `serving-llms-vllm`, `audiocraft-audio-generation`, `segment-anything-model`, `dspy`, `axolotl`, `fine-tuning-with-trl`, `unsloth` |
| `note-taking` | 1 | `obsidian` |
| `productivity` | 8 | `airtable`, `google-workspace`, `linear`, `maps`, `nano-pdf`, `notion`, `ocr-and-documents`, `powerpoint` |
| `red-teaming` | 1 | `godmode` |
| `research` | 5 | `arxiv`, `blogwatcher`, `llm-wiki`, `polymarket`, `research-paper-writing` |
| `smart-home` | 1 | `openhue` |
| `social-media` | 1 | `xurl` |
| `software-development` | 11 | `debugging-hermes-tui-commands`, `hermes-agent-skill-authoring`, `node-inspect-debugger`, `plan`, `python-debugpy`, `requesting-code-review`, `spike`, `subagent-driven-development`, `systematic-debugging`, `test-driven-development`, `writing-plans` |
| `yuanbao` | 1 | `yuanbao` |

### Optional Category Inventory

| Category | Count | Skills |
|---|---:|---|
| `autonomous-ai-agents` | 2 | `blackbox`, `honcho` |
| `blockchain` | 2 | `base`, `solana` |
| `communication` | 1 | `one-three-one-rule` |
| `creative` | 5 | `blender-mcp`, `concept-diagrams`, `hyperframes`, `kanban-video-orchestrator`, `meme-generation` |
| `devops` | 2 | `inference-sh-cli`, `docker-management` |
| `dogfood` | 1 | `adversarial-ux-test` |
| `email` | 1 | `agentmail` |
| `health` | 2 | `fitness-nutrition`, `neuroskill-bci` |
| `mcp` | 2 | `fastmcp`, `mcporter` |
| `migration` | 1 | `openclaw-migration` |
| `mlops` | 25 | `huggingface-accelerate`, `chroma`, `clip`, `faiss`, `optimizing-attention-flash`, `guidance`, `hermes-atropos-environments`, `huggingface-tokenizers`, `instructor`, `lambda-labs-gpu-cloud`, `llava`, `modal-serverless-gpu`, `nemo-curator`, `peft-fine-tuning`, `pinecone`, `pytorch-fsdp`, `pytorch-lightning`, `qdrant-vector-search`, `sparse-autoencoder-training`, `simpo-training`, `slime-rl-training`, `stable-diffusion`, `tensorrt-llm`, `distributed-llm-pretraining-torchtitan`, `whisper` |
| `productivity` | 6 | `canvas`, `here.now`, `memento-flashcards`, `shopify`, `siyuan`, `telephony` |
| `research` | 8 | `bioinformatics`, `domain-intel`, `drug-discovery`, `duckduckgo-search`, `gitnexus-explorer`, `parallel-cli`, `qmd`, `scrapling` |
| `security` | 3 | `1password`, `oss-forensics`, `sherlock` |
| `web-development` | 1 | `page-agent` |

## Takeaways For DeepCLI

1. **The shortest useful core is not the biggest library.**  Claude Code and
   Codex keep bundled/system skills small and focused on meta-workflows.  The
   large catalogs live better as installable/library skills.
2. **Directory `SKILL.md` is the convergence point.**  Codex, OpenClaw,
   Hermes, and DeepCLI's current design all support or align with a directory
   skill shape that can carry references, scripts, templates, and assets.
3. **Two kinds of built-ins should stay separate.**  Kernel-critical skills
   such as verify/debug/skill authoring should be bundled; user-domain
   integrations such as Spotify, Hue, Notion, or MLOps frameworks should be
   installable library skills.
4. **Progressive disclosure matters.**  Claude Code's lazy extraction and
   Hermes' category/supporting-file model both avoid dumping every domain
   detail into the base prompt.
5. **OpenClaw/Hermes are strong seed catalogs.**  OpenClaw is strongest for
   local CLI/platform adapters; Hermes is strongest for broad domain packs and
   rich optional skills.  DeepCLI should treat them as migration/import sources,
   not as a hardcoded kernel surface.
