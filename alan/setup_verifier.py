from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import TextIO

from alan import github_repos, secrets_store, setup_transport

LOGIN_KEY = "ALAN_VERIFIER_LOGIN="
LOGIN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
BOT_PROMPT = "Verifier bot token (input is hidden): "

INSTRUCTIONS_TEXT = "\n".join(
    [
        "The verifier bot is a second GitHub account, separate from yours, that reviews every",
        "pull request. Before you paste its token:",
        "  1. Invite the bot to this repository from your own account (Settings > Collaborators)",
        "     with the Write role.",
        "  2. Accept the invitation while signed in as the bot.",
        "  3. As the bot, create a classic personal access token with the repo scope and an",
        "     expiry: GitHub > Settings > Developer settings > Personal access tokens > Tokens",
        "     (classic).",
        "A fine-grained token does not work: it cannot reach a repository owned by another",
        "account. Paste the bot's token below. It is not shown, not printed and not passed on",
        "any command line; setup checks it with GitHub and stores it in alan-factory's own",
        "secret store as the reviewer credential.",
    ]
)
NO_EXPIRY_TEXT = (
    "GitHub reports no expiry for this token, so no expiry reminder will fire; "
    "rotate it yourself if you set no expiry on purpose"
)
_EMPTY_INPUT_MESSAGE = (
    "no verifier token was entered (empty input or end of input); run setup in a terminal to "
    "type it, or pipe it on standard input"
)
_FINE_GRAINED_MESSAGE = (
    "the token is a fine-grained personal access token; the verifier bot needs a classic token "
    "with the repo scope, because a fine-grained token cannot reach a repository owned by "
    "another account"
)
_SAME_ACCOUNT_MESSAGE = (
    "the token authenticates as the signed-in GitHub account; the verifier must be a separate "
    "GitHub account from the author"
)
_UNKNOWN_AUTHOR_MESSAGE = (
    "the signed-in GitHub account is unknown, so the bot cannot be checked against it"
)
_INVITED_MESSAGE = (
    "the bot has not accepted its invitation to this repository yet; sign in as the bot, "
    "accept the invitation and re-run"
)
_NO_ACCESS_MESSAGE = (
    "the bot account has no write access to this repository; invite it as a collaborator with "
    "the Write role, accept the invitation as the bot and re-run"
)
_LOGIN_SHAPE_MESSAGE = "the account name is not a plain GitHub login, so it cannot be recorded"
_SYMLINK_MESSAGE = (
    "the secrets file is a symbolic link; setup will not write through it, so replace it with a "
    "regular file and re-run"
)
_ACCESS_MESSAGES = {"invited": _INVITED_MESSAGE, "none": _NO_ACCESS_MESSAGE}


class VerifierRefused(RuntimeError):
    pass


def validated_login(login: str) -> str:
    if LOGIN_PATTERN.fullmatch(login) is None:
        raise VerifierRefused(_LOGIN_SHAPE_MESSAGE)
    return login


def reviewer_body(engine: str, token: str, expires_on: date | None) -> str:
    body: dict[str, str] = {
        "engine": engine,
        "purpose": secrets_store.REVIEWER_PURPOSE,
        "value": token,
    }
    if expires_on is not None:
        body["expires_at"] = expires_on.isoformat()
    return json.dumps(body)


def _identity(token: str) -> github_repos.TokenIdentity:
    try:
        return github_repos.probe_token_identity(token, timeout=setup_transport.GH_TIMEOUT_S)
    except github_repos.TokenIdentityError as exc:
        raise VerifierRefused(str(exc)) from None


def _require_classic_and_distinct(identity: github_repos.TokenIdentity, author: str) -> None:
    if identity.fine_grained:
        raise VerifierRefused(_FINE_GRAINED_MESSAGE)
    if not author:
        raise VerifierRefused(_UNKNOWN_AUTHOR_MESSAGE)
    if identity.login.casefold() == author.casefold():
        raise VerifierRefused(_SAME_ACCOUNT_MESSAGE)


def _require_write_access(token: str, engine: str) -> None:
    try:
        access = github_repos.probe_repo_access(
            token, engine, timeout=setup_transport.GH_TIMEOUT_S
        )
    except github_repos.RepoAccessError as exc:
        raise VerifierRefused(str(exc)) from None
    if access != "write":
        raise VerifierRefused(_ACCESS_MESSAGES[access])


def verified_bot(token: str, engine: str, author: str) -> github_repos.TokenIdentity:
    identity = _identity(token)
    _require_classic_and_distinct(identity, author)
    validated_login(identity.login)
    _require_write_access(token, engine)
    return identity


def print_expiry(expires_on: date | None) -> None:
    if expires_on is None:
        print(NO_EXPIRY_TEXT)
        return
    print(
        f"The token expires on {expires_on.isoformat()} (UTC); "
        "alan-factory warns you a week before."
    )


def run_verify(
    engine: str, author: str, body_out: Path, login_out: Path, *, stream: TextIO
) -> None:
    print(INSTRUCTIONS_TEXT)
    token = setup_transport.read_token(
        stream, missing=_EMPTY_INPUT_MESSAGE, prompt=BOT_PROMPT
    )
    identity = verified_bot(token, engine, author)
    print_expiry(identity.expires_on)
    setup_transport.write_private_file(login_out, identity.login)
    setup_transport.write_private_file(
        body_out, reviewer_body(engine, token, identity.expires_on)
    )


def _read_secrets_text(path: Path) -> str:
    if path.is_symlink():
        raise VerifierRefused(_SYMLINK_MESSAGE)
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return ""


def _is_login_line(segment: str) -> bool:
    return segment.startswith(LOGIN_KEY)


def _appended(raw: str, line: str) -> str:
    separator = "\n" if raw and not raw.endswith("\n") else ""
    return f"{raw}{separator}{line}\n"


def _first_login_index(segments: list[str]) -> int | None:
    return next((i for i, segment in enumerate(segments) if _is_login_line(segment)), None)


def _replaced(segments: list[str], first: int, wanted: str) -> str:
    carriage = "\r" if segments[first].endswith("\r") else ""
    segments[first] = wanted + carriage
    kept = [s for i, s in enumerate(segments) if i == first or not _is_login_line(s)]
    return "\n".join(kept)


def rewritten(raw: str, login: str) -> str:
    wanted = f"{LOGIN_KEY}{login}"
    segments = raw.split("\n")
    first = _first_login_index(segments)
    if first is None:
        return _appended(raw, wanted)
    return _replaced(segments, first, wanted)


def login_recorded(raw: str) -> bool:
    lines = [segment for segment in raw.split("\n") if _is_login_line(segment)]
    return len(lines) == 1 and bool(lines[0][len(LOGIN_KEY) :].strip())


def run_login_line(secrets_file: Path, login_file: Path) -> str:
    login = validated_login(login_file.read_text(encoding="utf-8").strip())
    raw = _read_secrets_text(secrets_file)
    updated = rewritten(raw, login)
    if updated == raw:
        return "unchanged"
    setup_transport.write_private_file(secrets_file, updated)
    return "changed"


def run_login_present(secrets_file: Path) -> bool:
    return login_recorded(_read_secrets_text(secrets_file))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m alan.setup_verifier")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--engine", required=True)
    verify.add_argument("--signed-in-user", default="")
    verify.add_argument("--body-out", type=Path, required=True)
    verify.add_argument("--login-out", type=Path, required=True)
    line = commands.add_parser("login-line")
    line.add_argument("--secrets-file", type=Path, required=True)
    line.add_argument("--login-file", type=Path, required=True)
    present = commands.add_parser("login-present")
    present.add_argument("--secrets-file", type=Path, required=True)
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "verify":
        run_verify(
            args.engine, args.signed_in_user, args.body_out, args.login_out, stream=sys.stdin
        )
    elif args.command == "login-line":
        print(run_login_line(args.secrets_file, args.login_file))
    elif not run_login_present(args.secrets_file):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except (VerifierRefused, setup_transport.TransportRefused, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
