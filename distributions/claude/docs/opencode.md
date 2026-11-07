<!-- Copyright 2026 Google LLC. SPDX-License-Identifier: Apache-2.0 -->

# OpenCode V2 integration (experimental)

This adapter targets OpenCode **2.x** and the V2 `agents`, `commands`,
`permissions`, and `subagent` interfaces. It is designed for Linux, macOS, and
Windows, and verified with OpenCode `2.2.6` on Windows in this workspace. The
CI native probe targets Linux. It does not target the V1 `task` or `permission`
interfaces.

Installation checks do not establish workflow or isolation support. Maintainer
approval, live workflows, and isolation verification remain pending under
[#146](https://github.com/google/elephant-goldfish-model/issues/146). The
proposed architecture is in [the additional harness design](https://github.com/google/elephant-goldfish-model/blob/main/docs/designs/additional-harnesses.md).

## Install

Use Python 3.10 or newer. The installer copies the complete canonical resource
trees and does not need symbolic links, so it works on Windows without Developer
Mode. It does not change global or project configuration. It refuses an existing
destination; use a new destination for each candidate.

POSIX shell (Linux and macOS):

```sh
git rev-parse HEAD
python3 adapters/opencode/install.py /absolute/egm-opencode-v2
opencode --version
```

Windows PowerShell:

```powershell
git rev-parse HEAD
python adapters\opencode\install.py "$env:TEMP\egm-opencode-v2"
opencode --version
```

Require OpenCode `2.x` before launch. The installer records the tested host
version in `installation.json`; newer OpenCode 2.x releases are expected to work,
but verify the generated configuration before relying on a different release.

Inspect the generated `opencode.json` and `installation.json` before use. The
latter records package identity, platform, Python version, and SHA-256 hashes of
all bundled files. The native configuration contains seven commands, eight
reviewers, and the `egm` primary agent. The bundle's `skills/` directory is
registered directly, so skill IDs are the canonical directory names.

## Run

OpenCode V2 normally connects to a shared background service. That service may
already be using another configuration, so use `--standalone` with
`OPENCODE_CONFIG` for an isolated candidate:

POSIX shell:

```sh
cd /absolute/path/to/project
OPENCODE_CONFIG=/absolute/egm-opencode-v2/opencode.json opencode --standalone
```

Windows PowerShell:

```powershell
cd C:\absolute\path\to\project
$env:OPENCODE_CONFIG = "$env:TEMP\egm-opencode-v2\opencode.json"
opencode --standalone
```

The generated configuration sets `default_agent` to `egm`, so the session opens
with that agent. For a non-interactive run, use
`opencode --standalone run --agent egm "..."`.

To use the candidate without `--standalone`, copy its generated `opencode.json`
into the project as `opencode.json` or `.opencode/opencode.json`. That is an
explicit project configuration change; keep the absolute bundle paths intact.

Select `egm` before native skill use. Other primary agents do not receive this
adapter's dispatch instructions. For review text, use an ordinary message rather
than a slash command. For example:

```text
Use the egm-review skill to review /absolute/path/design.md.
```

The generated `/egm-brainstorm`, `/egm-draft`, and `/egm-review` commands use
OpenCode's native command syntax. Their arguments are not literal text:
OpenCode evaluates shell markers (an exclamation mark followed by a command in
backticks) before agent tool permissions apply. Do not paste untrusted review
material into slash-command arguments. Markdown fences do not disable this
preprocessing. Paste material into an ordinary message, or save it in a file for
the agent to read.

To uninstall, stop sessions, remove only this candidate directory, and remove
its `OPENCODE_CONFIG` or copied project configuration.

## Verify the installation

List the effective configuration and agents with the private server:

```sh
OPENCODE_CONFIG=/absolute/egm-opencode-v2/opencode.json opencode --standalone debug config
OPENCODE_CONFIG=/absolute/egm-opencode-v2/opencode.json opencode --standalone debug agents
```

`debug agents` should list `egm`, `egm-codebase-details`, `egm-comprehension`,
`egm-design`, `egm-implementation-readiness`, `egm-inventive-catalyst`,
`egm-structural`, `egm-style`, and `egm-zoom-out`. It should not list
OpenCode's built-in general-purpose agent as an EGM reviewer.

Run the offline checks:

```sh
python -m pytest tests/unit/test_opencode_adapter.py
python -m pytest tests/unit
```

These tests check installation, resource identity, native configuration, path
normalization, and refusal to overwrite files. They do not simulate host
completion records or prove enforcement by the host. No live model test runs in
ordinary CI.

## Paths and dispatch

Use the absolute bundle root in the generated prompt. Resolve `agents/`,
`resources/`, and `docs/` against that root. Resolve `references/` against the
canonical skill directory. Resolve user files against the project directory.
Never substitute project files for bundled personas.

OpenCode V2's `subagent` tool takes an `agent` ID, a short `description`, and a
complete `prompt`. Dispatch reviewers directly from `egm`:

- Set `agent` to the registered persona name, such as `egm-zoom-out`.
- Omit `sessionID` to create a fresh child session. Never resume a reviewer.
- Do not set `background`; wait for foreground completion.
- Pass absolute paths for the persona, target, standards, and selected sources.
- Do not pass parent history, private reasoning, or memory. Supply prior report
  paths only when the canonical stage requires them, such as
  `zoom_out_report.md` for Step 8 (Intermediate Zoom Out).
- Read prior reports only when explicitly supplied and required by the canonical
  stage. Do not retrieve unsolicited history, memory, or earlier reviewer
  findings.

OpenCode's default `experimental.subagent_depth` is `1`, so a reviewer cannot
start another subagent. Do not dispatch the synthesizer; the `egm` primary agent
performs synthesis and writes reports. The skill's direct-dispatch path applies.

When brainstorm precedes draft, complete all seven brainstorm reviewers first.
Synthesize their findings into `concepts_brief.md` before drafting. For review,
write each completed stage report before starting the next stage. Before Step 8,
verify `zoom_out_report.md` exists in the artifact directory and pass its
absolute path to the reviewer. Stop if the report is missing.

Bootstrap worker and scout delegation is outside this adapter's scope. Only the
eight reviewer IDs are permitted as subagents. Run scout steps inline when the
skill allows it; if bootstrap requires an orchestrator or scout subagent, stop and
report the limitation. Do not substitute a reviewer or simulate the missing
worker.

Honor `EGM artifact directory: <path>` in the user's prompt or loaded
instructions. Resolve a relative output directory against the project directory.
Otherwise retain the canonical output filenames and working-directory default.
Preserve each readiness, section, and stage approval gate. Use the `question`
tool for a single fixed-choice gate; use plain text for open-ended input. Wait
for the user's answer. Missing tools and elapsed time do not constitute approval.

## Permissions

V2 uses one ordered `permissions` array; the last matching rule wins. The
adapter appends its rules after OpenCode's base policy.

Reviewers:

- Deny every action first.
- Allow `read` and `glob`.
- Deny `grep`, because grep searches bypass per-file read rules, including the
  `.env` exclusions. Use permitted file reads for review context.
- Deny `.env` and `.env.*` reads, while allowing `.env.example`.
- Deny external directories, then allow the installed bundle.
- The broad deny also closes MCP tools and `opencode_read_mcp_resource` /
  `opencode_list_mcp_resources`.
- The `skill` tool can remain visible in the reviewer's tool list, but the
  `skill` permission denies loading every skill.

The `egm` primary agent:

- Starts from `ask` for unspecified actions, then allows reads, `glob`, `grep`,
  questions, and the installed bundle.
- Keeps `.env` reads as `ask`.
- Allows only the seven canonical EGM skills and the eight reviewer IDs.
- Asks before writes, shell commands, web access, and other side effects.

V2 allows `external_directory` by default. This adapter restores a prompt for the
primary agent and a deny for reviewers, with an exception for the installed
bundle. Reviewers therefore cannot read a target or standard outside the
project. Move an external fixture into the project, or review a separate,
narrow permission change; do not widen the reviewer rule for a normal review.

OpenCode merges system, global, project, and managed configuration. Inspect the
effective configuration before a run; later settings can override these
permissions. Use a clean profile and a disposable project for verification.
Inspect inherited instructions, plugins, MCP services, memory, and shared files
separately. Read-only tool configuration is not process or filesystem isolation.
A fresh child session alone does not prove Goldfish isolation.

## Verification limits

A passing installation check does not establish live model behavior, full
workflows, or general reviewer isolation. Before a support claim, explicitly
authorize and complete these checks:

1. Record the candidate SHA, installed hashes, host version, model identifier,
   surface, and date.
2. Run brainstorm, draft, and review with an actual human approval pause.
3. Retain native reviewer starts, completions, required reads, and produced
   artifacts.
4. Probe write and shell denial against disposable files and inspect the
   effective child permissions.
5. Place a random marker only in the parent conversation and inspect child
   context and output.
6. Report installation, workflow, and isolation outcomes separately. Mark
   missing evidence as unverified.

The scoped native probe uses a scripted local model, not a live model. It checks
native child dispatch, one parent marker, an allowed persona read from the
installed bundle, and denial of a synthetic `.env` read. It requires the pinned
OpenCode V2 executable in `tests/integration/opencode-ci/`:

```sh
npm ci --ignore-scripts --prefix tests/integration/opencode-ci
python tests/integration/opencode_native_probe.py \
  --source . \
  --opencode tests/integration/opencode-ci/node_modules/@opencode/cli-linux-x64/bin/opencode \
  --output /tmp/egm-opencode-v2-probe
```

On Windows, point `--opencode` at the installed `opencode.cmd` or
`opencode.exe`. Choose an output path that does not exist. A passing probe does
not establish live model behavior, full workflows, or general reviewer
isolation.

## Windows notes

- The installer never creates symbolic links. Skill directories are registered
  directly from the bundle.
- Generated prompts and permission rules use forward-slash paths. OpenCode
  normalizes backslashes to slashes and matches paths case-insensitively on
  Windows.
- Use `$env:OPENCODE_CONFIG = "..."` for the current PowerShell session, and
  `Remove-Item Env:OPENCODE_CONFIG` to unset it.
- `--standalone` avoids using the shared background service and its possibly
  different configuration. `OPENCODE_CONFIG` alone is not enough when the
  shared service is already running.
- Paths with spaces or non-ASCII characters are supported. Paths containing
  template or glob characters (`* ? [ ] { } ! @ ` $`) and `.env.` are rejected.

## Differences from the V1 adapter

| V1 | V2 |
| --- | --- |
| `agent` map | `agents` map |
| `command` map | `commands` map |
| `permission` map | ordered `permissions` array |
| `task` action and tool | `subagent` action and tool |
| `autoupdate` | `update` |
| `skills.paths` | `skills` array |
| `prompt` | `system` |
| `task_id` | `sessionID` (omit for a fresh child) |

## Interface sources

Reviewed on 2026-11-07 against OpenCode `2.2.6`:

- [V2 migration guide](https://opencode.ai/docs/migrate-v1/)
- [V2 agents](https://opencode.ai/docs/agents/)
- [V2 permissions](https://opencode.ai/docs/permissions/)
- [V2 skills](https://opencode.ai/docs/skills/)
- [V2 commands](https://opencode.ai/docs/commands/)
- [V2 tools](https://opencode.ai/docs/tools/)
- [V2 configuration](https://opencode.ai/docs/config/)
