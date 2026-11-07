# Copyright 2026 Google LLC.
# SPDX-License-Identifier: Apache-2.0

"""Install an experimental OpenCode V2 bundle without changing user config."""

import argparse
import hashlib
import json
import platform
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
HOST_SERIES = "2"
HOST_VERSION = "2.2.6"
REVIEWERS = (
    "codebase_details", "comprehension", "design_critic",
    "implementation_readiness", "inventive_catalyst", "structural", "style", "zoom_out",
)


def name(path: Path) -> str:
    """Read the canonical name without a runtime YAML dependency."""
    header = path.read_text(encoding="utf-8").split("---", 2)[1]
    match = re.search(r"^name: (egm-[a-z-]+)$", header, re.MULTILINE)
    if not match:
        raise ValueError(f"Missing canonical name: {path}")
    return match[1]


def posix(path: Path) -> str:
    """Return a forward-slash path for OpenCode prompts and permission rules."""
    return path.as_posix()


def directory_rule(path: Path) -> str:
    """Return the V2 external-directory boundary for a directory."""
    return posix(path) + "/*"


def reviewer_permissions(bundle: Path) -> list[dict[str, str]]:
    """Deny everything, then allow the reads a Goldfish reviewer needs."""
    return [
        {"action": "*", "resource": "*", "effect": "deny"},
        {"action": "read", "resource": "*", "effect": "allow"},
        {"action": "read", "resource": "*.env", "effect": "deny"},
        {"action": "read", "resource": "*.env.*", "effect": "deny"},
        {"action": "read", "resource": "*.env.example", "effect": "allow"},
        {"action": "glob", "resource": "*", "effect": "allow"},
        # grep bypasses per-file read rules, including the .env exclusions.
        {"action": "grep", "resource": "*", "effect": "deny"},
        {"action": "external_directory", "resource": "*", "effect": "deny"},
        {"action": "external_directory", "resource": directory_rule(bundle), "effect": "allow"},
    ]


def orchestrator_permissions(
    bundle: Path, reviewer_ids: list[str], skill_ids: list[str]
) -> list[dict[str, str]]:
    """Ask before side effects while allowing only the EGM reviewers and skills."""
    rules = [
        {"action": "*", "resource": "*", "effect": "ask"},
        {"action": "read", "resource": "*", "effect": "allow"},
        {"action": "read", "resource": "*.env", "effect": "ask"},
        {"action": "read", "resource": "*.env.*", "effect": "ask"},
        {"action": "read", "resource": "*.env.example", "effect": "allow"},
        {"action": "glob", "resource": "*", "effect": "allow"},
        {"action": "grep", "resource": "*", "effect": "allow"},
        {"action": "question", "resource": "*", "effect": "allow"},
        {"action": "skill", "resource": "*", "effect": "deny"},
        *({"action": "skill", "resource": skill_id, "effect": "allow"} for skill_id in skill_ids),
        {"action": "subagent", "resource": "*", "effect": "deny"},
        *({"action": "subagent", "resource": reviewer_id, "effect": "allow"}
          for reviewer_id in reviewer_ids),
        {"action": "external_directory", "resource": "*", "effect": "ask"},
        {"action": "external_directory", "resource": directory_rule(bundle), "effect": "allow"},
    ]
    return rules


def validate_destination(destination: Path) -> Path:
    """Reject unsafe paths before creating anything."""
    destination = destination.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    try:
        destination = destination.resolve()
    except OSError as exc:
        raise ValueError(f"Invalid installation path: {destination}") from exc
    text = str(destination)
    # The path enters generated prompts and OpenCode permission rules.
    if any(character in text for character in '*?[]{}!@`$\n\r'):
        raise ValueError("Installation path contains template or glob characters")
    # Read rules match worktree-relative paths and "*" spans "/", so "*.env.*"
    # would deny every bundle read below such a path.
    if ".env." in text.lower():
        raise ValueError("Installation path matches the .env read rules")
    if destination.is_relative_to(ROOT):
        raise ValueError("Install outside the source tree")
    return destination


def install(destination: Path) -> Path:
    """Create a new installation; refuse to overwrite any existing path."""
    destination = validate_destination(destination)
    destination.mkdir(parents=True)
    bundle = destination / "bundle"
    bundle.mkdir()
    for directory in ("skills", "agents", "resources", "docs"):
        shutil.copytree(ROOT / directory, bundle / directory)
    for filename in ("plugin.json", "LICENSE", "README.md", "TESTING.md"):
        shutil.copy2(ROOT / filename, bundle / filename)

    guide = bundle / "docs/opencode.md"
    root = posix(bundle)
    context = (
        f"Read {posix(guide)} first. EGM bundle root: {root}. "
        "Resolve agents/, resources/, and docs/ against this root. "
        "Resolve references/ against the canonical skill directory. "
        "Resolve user files and outputs against the project working directory. "
    )

    reviewer_ids = []
    agents = {}
    for reviewer in REVIEWERS:
        persona = bundle / "agents" / f"{reviewer}.md"
        reviewer_id = name(persona)
        reviewer_ids.append(reviewer_id)
        agents[reviewer_id] = {
            "description": f"EGM read-only {reviewer} reviewer (experimental)",
            "mode": "subagent",
            "color": "#E86E00",
            "permissions": reviewer_permissions(bundle),
            "system": (
                context
                + f"Read {posix(persona)} before the target and supplied standards. "
                "Return findings to the caller. Do not write reports or delegate. "
                "Read prior reports only when explicitly supplied and required by the canonical stage. "
                "Do not retrieve unsolicited history, memory, or earlier reviewer findings. "
                "Report inaccessible required files as an incomplete review. "
                "OpenCode starts this subagent with a fresh context. Do not spawn subagents."
            ),
        }

    skill_ids = []
    commands = {}
    for skill in sorted((bundle / "skills").glob("*/SKILL.md")):
        skill_id = name(skill)
        if skill.parent.name != skill_id:
            raise ValueError(
                f"Skill directory {skill.parent.name!r} must match canonical name {skill_id!r}"
            )
        skill_ids.append(skill_id)
        commands[skill_id] = {
            "description": f"Run {skill_id} (experimental OpenCode V2)",
            "agent": "egm",
            "template": context + f"Read and execute {posix(skill)}.\nUser request: $ARGUMENTS",
        }

    agents["egm"] = {
        "description": "EGM orchestrator (experimental OpenCode V2)",
        "mode": "primary",
        "color": "#1B6EF3",
        "permissions": orchestrator_permissions(bundle, reviewer_ids, skill_ids),
        "system": (
            context
            + "Follow the canonical skill. OpenCode V2 starts each subagent in a fresh child session. "
            "Dispatch reviewers directly with the subagent tool: set its agent field to the registered "
            "agent ID (for example egm-zoom-out), and supply a short description and a complete prompt. "
            "Do not use the synthesizer: OpenCode's default subagent depth is 1, so a child cannot "
            "start reviewers. Do not set background; wait for foreground completion. Omit sessionID "
            "and never resume a reviewer. Pass only the task, absolute resource paths, target, "
            "standards, and selected reading list, not conversation history or private reasoning. "
            "Stop if delegation or a required completion is missing. "
            "Use the question tool for a single fixed-choice approval gate; use plain text for "
            "open-ended input. "
            "When running brainstorm before draft, complete all seven brainstorm reviewers and "
            "synthesize their findings into concepts_brief.md before drafting. "
            "In review, write each completed stage report before starting the next stage. "
            "Before Step 8, verify zoom_out_report.md exists in the artifact directory and pass "
            "its absolute path to the reviewer; stop if it is missing. "
            "Write reports yourself. Preserve every human approval gate. "
            "Bootstrap worker and scout delegation is out of scope: only the eight reviewer IDs "
            "are permitted. Run scout steps inline when the skill allows it; if bootstrap requires "
            "a worker subagent, stop and report the limitation instead of substituting a reviewer."
        ),
    }

    config = destination / "opencode.json"
    config.write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "update": "disable",
        "default_agent": "egm",
        "skills": [posix(bundle / "skills")],
        "agents": agents,
        "commands": commands,
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    metadata = json.loads((bundle / "plugin.json").read_text(encoding="utf-8"))
    (destination / "installation.json").write_text(json.dumps({
        "name": metadata["name"], "version": metadata["version"],
        "host_series": HOST_SERIES, "host_version": HOST_VERSION,
        "status": "experimental",
        "platform": sys.platform, "python": platform.python_version(),
        "sha256": {
            path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(bundle.rglob("*")) if path.is_file()
        },
    }, indent=2) + "\n", encoding="utf-8", newline="\n")
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="New, fixed installation directory")
    print(install(parser.parse_args().destination))
