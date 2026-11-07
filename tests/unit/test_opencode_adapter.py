# Copyright 2026 Google LLC.
# SPDX-License-Identifier: Apache-2.0

"""Offline checks for the experimental OpenCode V2 installer."""

import hashlib
import json
import os
import platform
from pathlib import Path
import re
import runpy
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADAPTER = runpy.run_path(str(ROOT / "adapters/opencode/install.py"))


def reviewer_rules(bundle: Path) -> list[dict[str, str]]:
    return [
        {"action": "*", "resource": "*", "effect": "deny"},
        {"action": "read", "resource": "*", "effect": "allow"},
        {"action": "read", "resource": "*.env", "effect": "deny"},
        {"action": "read", "resource": "*.env.*", "effect": "deny"},
        {"action": "read", "resource": "*.env.example", "effect": "allow"},
        {"action": "glob", "resource": "*", "effect": "allow"},
        {"action": "grep", "resource": "*", "effect": "deny"},
        {"action": "external_directory", "resource": "*", "effect": "deny"},
        {"action": "external_directory", "resource": bundle.as_posix() + "/*", "effect": "allow"},
    ]


def test_install_preserves_resources_and_native_contract(tmp_path: Path) -> None:
    destination = tmp_path / "EGM candidate ü"
    config_path = ADAPTER["install"](destination)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    identity = json.loads((destination / "installation.json").read_text(encoding="utf-8"))
    canonical = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert (identity["name"], identity["version"]) == (canonical["name"], canonical["version"])
    assert identity["host_series"] == "2"
    assert identity["host_version"] == "2.2.6"
    assert identity["platform"] == sys.platform
    assert identity["python"] == platform.python_version()
    for relative, digest in identity["sha256"].items():
        installed = destination / relative
        source = ROOT / installed.relative_to(destination / "bundle")
        assert installed.read_bytes() == source.read_bytes()
        assert hashlib.sha256(installed.read_bytes()).hexdigest() == digest
    for directory in ("skills", "agents", "resources", "docs"):
        for source in (ROOT / directory).rglob("*"):
            if source.is_file():
                assert (Path("bundle") / source.relative_to(ROOT)).as_posix() in identity["sha256"]

    bundle = destination / "bundle"
    assert config["$schema"] == "https://opencode.ai/config.json"
    assert config["default_agent"] == "egm"
    assert config["update"] == "disable"
    assert config["skills"] == [(bundle / "skills").as_posix()]
    assert "model" not in config
    assert "agent" not in config
    assert "command" not in config
    assert "permission" not in config
    assert len(config["commands"]) == 7
    for skill in (ROOT / "skills").glob("*/SKILL.md"):
        key = ADAPTER["name"](skill)
        assert skill.parent.name == key
        command = config["commands"][key]
        assert command["agent"] == "egm"
        assert (bundle / "skills" / skill.parent.name / "SKILL.md").as_posix() in command["template"]
        assert "$ARGUMENTS" in command["template"]
        assert "experimental OpenCode V2" in command["description"]

    assert len(config["agents"]) == 9
    reviewer_ids = {
        ADAPTER["name"](bundle / "agents" / f"{reviewer}.md")
        for reviewer in ADAPTER["REVIEWERS"]
    }
    for filename in ADAPTER["REVIEWERS"]:
        persona = bundle / "agents" / f"{filename}.md"
        agent = config["agents"][ADAPTER["name"](persona)]
        assert agent["mode"] == "subagent"
        assert agent["permissions"] == reviewer_rules(bundle)
        assert persona.as_posix() in agent["system"]
        assert "Read-only Protocol" not in agent["system"]
        assert "Do not spawn subagents." in agent["system"]
        assert "prompt" not in agent
        assert "permission" not in agent

    primary = config["agents"]["egm"]
    assert primary["mode"] == "primary"
    assert primary["permissions"][0] == {"action": "*", "resource": "*", "effect": "ask"}
    assert {"action": "read", "resource": "*", "effect": "allow"} in primary["permissions"]
    assert {"action": "question", "resource": "*", "effect": "allow"} in primary["permissions"]
    assert {"action": "external_directory", "resource": "*", "effect": "ask"} in primary["permissions"]
    assert {
        "action": "external_directory",
        "resource": (bundle / "*").as_posix(),
        "effect": "allow",
    } in primary["permissions"]
    subagent_rules = [rule for rule in primary["permissions"] if rule["action"] == "subagent"]
    assert subagent_rules[0] == {"action": "subagent", "resource": "*", "effect": "deny"}
    assert {rule["resource"] for rule in subagent_rules[1:]} == reviewer_ids
    skill_rules = [rule for rule in primary["permissions"] if rule["action"] == "skill"]
    assert skill_rules[0] == {"action": "skill", "resource": "*", "effect": "deny"}
    assert {rule["resource"] for rule in skill_rules[1:]} == set(config["commands"])
    assert "agent field" in primary["system"]
    assert "sessionID" in primary["system"]
    assert "foreground completion" in primary["system"]
    assert "Do not use the synthesizer" in primary["system"]
    assert "Bootstrap worker and scout delegation is out of scope" in primary["system"]


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_configuration_uses_forward_slash_paths(tmp_path: Path) -> None:
    destination = tmp_path / "candidate"
    config = json.loads(ADAPTER["install"](destination).read_text(encoding="utf-8"))
    assert not any("\\" in value for value in _strings(config))


def test_stage_required_reports_preserve_context_boundary(tmp_path: Path) -> None:
    destination = tmp_path / "candidate"
    config = json.loads(ADAPTER["install"](destination).read_text(encoding="utf-8"))
    bundle = destination / "bundle"
    canonical = (bundle / "skills/egm-review/SKILL.md").read_text(encoding="utf-8")
    assert "include `zoom_out_report.md` path in" in canonical
    guide = " ".join((bundle / "docs/opencode.md").read_text(encoding="utf-8").split())
    prompt = config["agents"]["egm-zoom-out"]["system"]
    for instructions in (prompt, guide):
        assert (
            "Read prior reports only when explicitly supplied and required by the canonical stage."
        ) in instructions
        assert "Do not retrieve unsolicited history, memory, or earlier reviewer findings." in instructions
    assert "Do not pass parent history, private reasoning, or memory." in guide


def test_existing_destination_is_untouched(tmp_path: Path) -> None:
    sentinel = tmp_path / "existing.txt"
    sentinel.write_text("other worker")
    with pytest.raises(FileExistsError):
        ADAPTER["install"](tmp_path)
    assert list(tmp_path.iterdir()) == [sentinel]
    assert sentinel.read_text() == "other worker"


@pytest.mark.parametrize("suffix", ["bad*", "bad?", "bad[glob]", "{env:HOME}", "bad\npath"])
def test_template_paths_are_rejected(tmp_path: Path, suffix: str) -> None:
    with pytest.raises(ValueError):
        ADAPTER["install"](tmp_path / suffix)
    assert not list(tmp_path.iterdir())


def test_source_destination_is_rejected() -> None:
    with pytest.raises(ValueError, match="outside the source"):
        ADAPTER["install"](ROOT / "unused-opencode-test-destination")


def test_command_interpolation_paths_are_rejected(tmp_path: Path) -> None:
    for component in ("$ARGUMENTS", "$1"):
        with pytest.raises(ValueError, match="template or glob"):
            ADAPTER["install"](tmp_path / component / "candidate")
    assert not list(tmp_path.iterdir())


def test_symlink_parent_cannot_bypass_path_validation(tmp_path: Path) -> None:
    target = tmp_path / "unsafe[glob]"
    target.mkdir()
    alias = tmp_path / "safe"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    with pytest.raises(ValueError):
        ADAPTER["install"](alias / "candidate")
    assert not list(target.iterdir())


def test_dangling_destination_is_untouched(tmp_path: Path) -> None:
    destination = tmp_path / "candidate"
    try:
        destination.symlink_to(tmp_path / "absent")
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")
    with pytest.raises(FileExistsError):
        ADAPTER["install"](destination)
    assert destination.is_symlink()
    assert not (tmp_path / "absent").exists()


def v2_match(pattern: str, value: str) -> bool:
    """Port of V2 whole-value wildcards: "*" spans "/", "?" matches one."""
    escaped = re.escape(pattern.replace("\\", "/")).replace(r"\*", ".*").replace(r"\?", ".")
    flags = re.S | (re.I if os.name == "nt" else 0)
    return re.fullmatch(escaped, value.replace("\\", "/"), flags) is not None


def v2_effect(rules: list[dict[str, str]], action: str, resource: str) -> str | None:
    effect = None
    for rule in rules:
        if v2_match(rule["action"], action) and v2_match(rule["resource"], resource):
            effect = rule["effect"]
    return effect


def test_required_reads_pass_env_rules(tmp_path: Path) -> None:
    destination = tmp_path / "candidate"
    config = json.loads(ADAPTER["install"](destination).read_text(encoding="utf-8"))
    bundle = destination / "bundle"
    required = [
        bundle / "docs/opencode.md",
        *(bundle / "agents").glob("*.md"),
        *(bundle / "resources").rglob("*.md"),
    ]
    reviewer_ids = {
        ADAPTER["name"](bundle / "agents" / f"{reviewer}.md")
        for reviewer in ADAPTER["REVIEWERS"]
    }
    for name, agent in config["agents"].items():
        rules = agent["permissions"]
        for path in required:
            resource = path.as_posix()
            assert v2_effect(rules, "external_directory", resource) == "allow", (name, path)
            assert v2_effect(rules, "read", resource) == "allow", (name, path)
        secret = "ask" if name == "egm" else "deny"
        for path in (".env", "app/.env", ".env.local", "app/.env.production", "prod.env"):
            assert v2_effect(rules, "read", path) == secret, (name, path)
        assert v2_effect(rules, "read", "app/.env.example") == "allow"
        if name == "egm":
            assert v2_effect(rules, "question", "*") == "allow"
            assert v2_effect(rules, "grep", "*") == "allow"
            assert v2_effect(rules, "edit", "*") == "ask"
            assert v2_effect(rules, "opencode_read_mcp_resource", "*") == "ask"
            assert v2_effect(rules, "opencode_list_mcp_resources", "*") == "ask"
            for reviewer_id in reviewer_ids:
                assert v2_effect(rules, "subagent", reviewer_id) == "allow"
            assert v2_effect(rules, "subagent", "general") == "deny"
            assert v2_effect(rules, "skill", "egm-review") == "allow"
            assert v2_effect(rules, "skill", "other") == "deny"
        else:
            assert v2_effect(rules, "question", "*") == "deny"
            assert v2_effect(rules, "grep", "*") == "deny"
            assert v2_effect(rules, "edit", "*") == "deny"
            assert v2_effect(rules, "shell", "*") == "deny"
            assert v2_effect(rules, "subagent", "*") == "deny"
            assert v2_effect(rules, "skill", "egm-review") == "deny"
            assert v2_effect(rules, "opencode_read_mcp_resource", "*") == "deny"
            assert v2_effect(rules, "opencode_list_mcp_resources", "*") == "deny"


@pytest.mark.parametrize("suffix", ["egm.env.candidate", "staging.ENV.d/candidate"])
def test_env_matching_destination_is_rejected(tmp_path: Path, suffix: str) -> None:
    with pytest.raises(ValueError, match="read rules"):
        ADAPTER["install"](tmp_path / suffix)
    assert not list(tmp_path.iterdir())
