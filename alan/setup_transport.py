from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import TextIO

from alan import (
    control_token_store,
    github_repos,
    roots,
    secrets_store,
    store,
)
from alan.control_plane import CONTROL_TOKEN_HEADER

GH_TIMEOUT_S = 30.0
PRIVATE_FILE_MODE = 0o600
STDIN_FD = 0
TOKEN_PROMPT = "Fine-grained token (input is hidden): "  # noqa: S105  # nosec B105

PERMISSIONS_TEXT = "\n".join(
    [
        "Create a fine-grained personal access token that reaches only this repository:",
        "  GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens",
        "  Repository access: Only select repositories, then pick this repository and no other.",
        "  Repository permissions (leave every other permission at No access):",
        "    Administration: Read and write",
        "    Contents: Read and write",
        "    Pull requests: Read and write",
        "    Issues: Read and write",
        "    Actions: Read and write",
        "    Workflows: Read and write",
        "    Commit statuses: Read-only",
        "    Metadata: Read-only (GitHub adds it by itself)",
        "Why Administration write: alan-factory applies your branch protection and the",
        "squash-merge and branch-deletion settings through GitHub's administration API, and no",
        "other permission allows that. Without it the protect step fails.",
        "Paste the token below. It is not shown, not printed and not passed on any command line;",
        "setup checks it with GitHub once and stores it in alan-factory's own secret store.",
    ]
)
RISK_TEXT = "\n".join(
    [
        "You asked setup to use your own gh token as alan-factory's transport credential",
        "(--use-gh-token). That token reaches every repository your GitHub account can reach,",
        "not only this one. It is copied into alan-factory's secret store under ~/.alan",
        "(mode 600) and stays there; every slot clone, fetch and push of this copy uses it.",
        "A fine-grained token scoped to this repository limits the damage if the store leaks,",
        "so prefer it. Revoke the copy when you uninstall.",
    ]
)
_GH_SESSION_MESSAGE = "the gh session token cannot be read; sign in with gh auth login"
_EMPTY_INPUT_MESSAGE = (
    "no token was entered (empty input or end of input); run setup in a terminal to type it, "
    "or pass --use-gh-token to use your gh token"
)
_NOT_FINE_GRAINED_MESSAGE = (
    "the token is not a fine-grained personal access token; create one for this repository, "
    "or pass --use-gh-token to use your gh token"
)
_WRONG_ACCOUNT_MESSAGE = (
    "the token authenticates as a different GitHub account than the signed-in gh user"
)


class TransportRefused(RuntimeError):
    pass


def write_private_file(path: Path, text: str) -> None:
    store.write_text_mkstemp(path, text, prefix=".setup_", mode=PRIVATE_FILE_MODE)


def control_header_text() -> str:
    path = roots.state_root() / control_token_store.CONTROL_TOKEN_FILENAME
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError:
        raise TransportRefused("the control token cannot be read; run the start step") from None
    if not token:
        raise TransportRefused("the control token file is empty; run the start step again")
    return f"{CONTROL_TOKEN_HEADER}: {token}\n"


def _read_byte(fd: int) -> bytes:
    try:
        return os.read(fd, 1)
    except OSError:
        return b""


def read_fd0_line(fd: int = STDIN_FD) -> str:
    collected = bytearray()
    while (byte := _read_byte(fd)) not in (b"", b"\n"):
        collected += byte
    return collected.decode("utf-8", errors="replace")


def _backing_fd(stream: TextIO) -> int | None:
    try:
        return stream.fileno()
    except (OSError, ValueError):
        return None


def _read_line(stream: TextIO, prompt: str) -> str:
    if stream.isatty():
        try:
            return getpass.getpass(prompt)
        except EOFError:
            return ""
    fd = _backing_fd(stream)
    return stream.readline() if fd is None else read_fd0_line(fd)


def read_token(
    stream: TextIO, *, missing: str = _EMPTY_INPUT_MESSAGE, prompt: str = TOKEN_PROMPT
) -> str:
    token = _read_line(stream, prompt).strip()
    if not token:
        raise TransportRefused(missing)
    return token


def verified_token(token: str, signed_in_user: str) -> str:
    try:
        identity = github_repos.probe_token_identity(token, timeout=GH_TIMEOUT_S)
    except github_repos.TokenIdentityError as exc:
        raise TransportRefused(str(exc)) from None
    if not identity.fine_grained:
        raise TransportRefused(_NOT_FINE_GRAINED_MESSAGE)
    if identity.login.casefold() != signed_in_user.casefold():
        raise TransportRefused(_WRONG_ACCOUNT_MESSAGE)
    return token


def transport_body(engine: str, token: str) -> str:
    return json.dumps(
        {"engine": engine, "purpose": secrets_store.TRANSPORT_PURPOSE, "value": token}
    )


def _obtain_token(*, use_gh_token: bool, signed_in_user: str, stream: TextIO) -> str:
    if use_gh_token:
        print(RISK_TEXT)
        return read_token(stream, missing=_GH_SESSION_MESSAGE)
    print(PERMISSIONS_TEXT)
    return verified_token(read_token(stream), signed_in_user)


def run_control_header(out: Path) -> None:
    write_private_file(out, control_header_text())


def run_transport_body(
    engine: str, out: Path, *, use_gh_token: bool, signed_in_user: str, stream: TextIO
) -> None:
    token = _obtain_token(
        use_gh_token=use_gh_token, signed_in_user=signed_in_user, stream=stream
    )
    write_private_file(out, transport_body(engine, token))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m alan.setup_transport")
    commands = parser.add_subparsers(dest="command", required=True)
    header = commands.add_parser("control-header")
    header.add_argument("--out", type=Path, required=True)
    body = commands.add_parser("transport-body")
    body.add_argument("--engine", required=True)
    body.add_argument("--out", type=Path, required=True)
    body.add_argument("--use-gh-token", action="store_true")
    body.add_argument("--signed-in-user", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "control-header":
            run_control_header(args.out)
        else:
            run_transport_body(
                args.engine,
                args.out,
                use_gh_token=args.use_gh_token,
                signed_in_user=args.signed_in_user,
                stream=sys.stdin,
            )
    except (TransportRefused, OSError) as exc:
        print(f"setup_transport: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
