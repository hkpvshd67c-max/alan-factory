from __future__ import annotations

import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SECURITY = _REPO_ROOT / "docs" / "tutorials" / "security.md"
_DOCKER = _REPO_ROOT / "docs" / "roadmap" / "docker.md"
_DEPLOY = _REPO_ROOT / "docs" / "architecture" / "deploy.md"
_PREP_CHAT = _REPO_ROOT / "docs" / "architecture" / "prep_chat.md"
_BIND_SECTION_START = "## Bind host/port override chain"
_BIND_SECTION_END = "## One-time bootstrap"
_HOST_ALLOWLIST_START = "## Host allowlist"
_HOST_ALLOWLIST_END = "## Non-goals"
_REMOVED_OPT_IN = "ALAN_ALLOW_NON_LOOPBACK_BIND"
_SCANNED_ROOTS = (
    "alan",
    "scripts",
    "deploy",
    ".github/workflows",
    "docs/tutorials",
    "docs/architecture",
)
_PRUNED_DIRECTORIES = frozenset({"node_modules", "__pycache__", ".venv"})


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split()).lower()


def _section(path: Path, heading: str, next_heading: str | None = None) -> str:
    text = _normalized(path)
    after = text.split(heading.lower(), 1)[1]
    if next_heading is not None:
        return after.split(next_heading.lower(), 1)[0]
    return after.split(" ## ", 1)[0]


def _line_containing(path: Path, marker: str) -> str:
    matching = [line for line in path.read_text(encoding="utf-8").splitlines() if marker in line]
    assert len(matching) == 1
    return " ".join(matching[0].split()).lower()


@pytest.mark.parametrize(
    "clause",
    [
        "any local account or process that can reach the port can read the api and can also "
        "create, dispatch, stop and delete work",
        "runs agents with shell access as the user alan-factory runs as",
        "running alan-factory as a dedicated macos user is the mitigation",
    ],
)
def test_security_tutorial_states_the_dispatch_exposure(clause: str) -> None:
    assert clause in _normalized(_SECURITY)


@pytest.mark.parametrize(
    "category",
    [
        "repository and engine administration",
        "secrets",
        "the control token itself",
        "the operator terminal",
        "onboarding",
        "a few workspace actions, such as answering a question or attaching a pull request",
        "not as a closed list",
    ],
)
def test_security_tutorial_describes_the_token_routes_by_category(category: str) -> None:
    assert category in _normalized(_SECURITY)


@pytest.mark.parametrize(
    "group",
    [
        "workspace create, dispatch, complete, delete, unarchive, notes, stop, resume and archive",
        "build chat and explore chat sessions",
        "project create, patch, complete, delete and amendments",
        "the workspace reindex",
    ],
)
def test_security_tutorial_names_the_routes_that_need_no_token(group: str) -> None:
    text = _normalized(_SECURITY)
    assert "these need no control token" in text
    assert group in text


def test_security_tutorial_says_the_host_check_is_not_authentication() -> None:
    text = _normalized(_SECURITY)
    assert "that check is not authentication" in text
    assert "can send any host header" in text


@pytest.mark.parametrize(
    "clause",
    [
        "forwarding the loopback port",
        "an ssh tunnel",
        "tailnet or reverse-proxy publish",
        "every client of that forward the same unauthenticated read-and-dispatch access",
        "the bind guard does not stop this",
    ],
)
def test_security_tutorial_warns_that_forwarding_bypasses_the_bind_guard(clause: str) -> None:
    assert clause in _normalized(_SECURITY)


@pytest.mark.parametrize(
    "clause",
    [
        "alan-factory also refuses a request other than get, head or options when it comes "
        "from a page on another site",
        "a local process that is not a browser, or a page served from a loopback hostname on any "
        "port, can still start agents, stop them and delete their work without the token",
    ],
)
def test_security_tutorial_states_the_cross_site_refusal_and_its_narrowed_reach(
    clause: str,
) -> None:
    assert clause in _normalized(_SECURITY)


@pytest.mark.parametrize(
    "clause",
    [
        "any method other than get, head or options",
        "its hostname must be a loopback name or the hostname of the preparation chat origin",
        "the port is ignored",
        "the value must be `same-origin` or `none`",
        "a request that carries neither header is served",
        "403` with `origin not allowed",
        "a local process that is not a browser still needs no token",
        "any page served from a loopback hostname on any port passes the origin check",
        "a page on another site cannot read the response to a request it sends to alan-factory",
        "alan-factory also refuses the write itself",
    ],
)
def test_security_tutorial_cross_site_section_states_the_refusal_and_its_limits(
    clause: str,
) -> None:
    assert clause in _section(_SECURITY, "## Cross-site requests")


@pytest.mark.parametrize(
    "untrusted_input",
    [
        "issue bodies",
        "pull request and review comments, including the comments of copilot",
        "dependabot pull requests",
        "pages that the research roles fetch",
    ],
)
def test_security_tutorial_prompt_injection_section_names_each_untrusted_input(
    untrusted_input: str,
) -> None:
    assert untrusted_input in _section(_SECURITY, "## Prompt injection")


@pytest.mark.parametrize(
    "clause",
    [
        "run shell commands with no operating-system sandbox",
        "are prefix matches",
        "posture `propose`",
        "default in the code, when no posture is set, is `observe`",
        "they apply on a public copy only",
        "last at most six months",
        "are not renewed automatically",
        "re-running setup applies them again",
        "a dedicated macos user",
        "a private repository",
    ],
)
def test_security_tutorial_prompt_injection_section_states_the_exposure_and_each_defence(
    clause: str,
) -> None:
    assert clause in _section(_SECURITY, "## Prompt injection")


@pytest.mark.parametrize(
    "marker",
    ["the bind host (`alan/bind.py`, `ALAN_HOST`)", "The `alan` program runs `deploy/run_alan.sh`"],
)
def test_docker_roadmap_names_no_opt_variable_and_defers_it_to_d7_authentication(
    marker: str,
) -> None:
    line = _line_containing(_DOCKER, marker)
    assert _REMOVED_OPT_IN.lower() not in line
    assert "unconditionally" in line
    assert "container-mode authentication item (d7)" in line


def _deploy_bind_section() -> str:
    return _section(_DEPLOY, _BIND_SECTION_START, _BIND_SECTION_END)


def test_deploy_doc_bind_section_describes_the_unconditional_guard_without_a_variable() -> None:
    section = _deploy_bind_section()
    assert "bind guard" in section
    assert "a custom alias that a hosts file maps to loopback is refused" in section
    assert "whatever the environment contains" in section
    assert "container-mode authentication item (d7)" in section
    assert "crash-loops" in section
    assert _REMOVED_OPT_IN.lower() not in section
    assert "the opt-in" not in section


def test_deploy_doc_names_bind_py_as_the_reader_of_the_bind_variables() -> None:
    text = _normalized(_DEPLOY)
    assert "`alan/bind.py` is the sole reader of both" in text
    assert "`alan/__main__.py` is the sole reader" not in text


def test_deploy_doc_tuning_knobs_say_a_non_loopback_host_is_refused_with_no_opt_in_entry() -> None:
    host_entry = _line_containing(_DEPLOY, "- `ALAN_HOST` / `ALAN_PORT`")
    assert "refused at startup." in host_entry
    assert "unless" not in host_entry
    assert _REMOVED_OPT_IN.lower() not in _normalized(_DEPLOY)


def test_prep_chat_doc_no_longer_presents_a_non_loopback_bind_as_supported() -> None:
    text = _normalized(_PREP_CHAT)
    assert "refuses to start on a non-loopback address" in text
    assert "a non-loopback bind is not a supported mode" in text
    assert "binding the service to a non-loopback address" not in text


def test_prep_chat_host_allowlist_section_has_no_opt_in() -> None:
    section = _section(_PREP_CHAT, _HOST_ALLOWLIST_START, _HOST_ALLOWLIST_END)
    assert "opt-in" not in section
    assert _REMOVED_OPT_IN.lower() not in section


@pytest.mark.parametrize(
    "clause",
    [
        "whose method is not exactly `get`, `head` or `options`",
        "an exact-case allowlist",
        "`scope[\"method\"]` is read strictly",
        "if an `origin` header is present, it decides alone and `sec-fetch-site` is ignored",
        "exactly `same-origin` or `none`",
        "a request with neither header is served",
        "403 {\"detail\": \"origin not allowed\"}",
        "host first (`400`), then the websocket `origin` rule, then the cross-site write rule",
        "any page served from a loopback hostname on any port passes",
        "a non-browser local process still needs no token",
        "assumes `get`, `head` and `options` do not change state",
        "set `prep_chat_origin` to its origin",
    ],
)
def test_prep_chat_host_allowlist_section_describes_the_cross_site_write_refusal(
    clause: str,
) -> None:
    section = _section(_PREP_CHAT, _HOST_ALLOWLIST_START, _HOST_ALLOWLIST_END)
    assert clause in section


def _shipped_files(relative: str) -> list[Path]:
    found: list[Path] = []
    for directory, subdirectories, filenames in os.walk(_REPO_ROOT / relative):
        subdirectories[:] = [
            name
            for name in subdirectories
            if name not in _PRUNED_DIRECTORIES and not name.startswith("dist")
        ]
        found.extend(Path(directory) / name for name in filenames)
    return found


def test_every_scanned_root_contributes_files() -> None:
    empty = [relative for relative in _SCANNED_ROOTS if not _shipped_files(relative)]
    assert empty == []


def test_removed_opt_in_name_appears_in_no_shipped_file() -> None:
    files = [path for relative in _SCANNED_ROOTS for path in _shipped_files(relative)]
    files.append(_DOCKER)
    offenders = [
        str(path.relative_to(_REPO_ROOT))
        for path in files
        if _REMOVED_OPT_IN in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []
