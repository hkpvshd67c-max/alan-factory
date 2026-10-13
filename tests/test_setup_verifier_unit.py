from __future__ import annotations

import io
import json
import stat
import sys
from datetime import date
from pathlib import Path

import pytest

from alan import github_repos, setup_transport, setup_verifier
from tests._setup_support import BOT_SENTINEL, leaks

BOT = "example-verifier"
AUTHOR = "example-owner"
ENGINE = "example-owner/example-repo"
KEY = "ALAN_VERIFIER_LOGIN="


def _identity(
    monkeypatch: pytest.MonkeyPatch,
    *,
    login: str = BOT,
    fine_grained: bool = False,
    expires_on: date | None = None,
    error: str | None = None,
) -> list[str]:
    seen: list[str] = []

    def _fake(token: str, *, timeout: float) -> github_repos.TokenIdentity:
        seen.append(token)
        if error is not None:
            raise github_repos.TokenIdentityError(error)
        return github_repos.TokenIdentity(
            login=login, fine_grained=fine_grained, expires_on=expires_on
        )

    monkeypatch.setattr(github_repos, "probe_token_identity", _fake)
    return seen


def _access(
    monkeypatch: pytest.MonkeyPatch, result: github_repos.RepoAccess | None
) -> list[tuple[str, str]]:
    seen: list[tuple[str, str]] = []

    def _fake(token: str, slug: str, *, timeout: float) -> github_repos.RepoAccess:
        seen.append((token, slug))
        if result is None:
            raise github_repos.RepoAccessError("GitHub could not be reached")
        return result

    monkeypatch.setattr(github_repos, "probe_repo_access", _fake)
    return seen


def _verify(tmp_path: Path, text: str = BOT_SENTINEL + "\n") -> tuple[Path, Path]:
    body, login = tmp_path / "reviewer.body", tmp_path / "reviewer.login"
    setup_verifier.run_verify(ENGINE, AUTHOR, body, login, stream=io.StringIO(text))
    return body, login


def test_a_distinct_classic_writer_is_accepted_and_both_checks_saw_the_typed_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identities = _identity(monkeypatch, expires_on=date(2099, 6, 30))
    accesses = _access(monkeypatch, "write")
    found = setup_verifier.verified_bot(BOT_SENTINEL, ENGINE, AUTHOR)
    assert found.login == BOT
    assert found.expires_on == date(2099, 6, 30)
    assert identities == [BOT_SENTINEL]
    assert accesses == [(BOT_SENTINEL, ENGINE)]


@pytest.mark.parametrize(
    ("kwargs", "author", "message"),
    [
        ({"fine_grained": True}, AUTHOR, "fine-grained personal access token"),
        ({}, "", "signed-in GitHub account is unknown"),
        ({"login": "Example-Owner"}, AUTHOR, "authenticates as the signed-in GitHub account"),
        ({"login": "not a login"}, AUTHOR, "not a plain GitHub login"),
        ({"login": "x\nALAN_VERIFIER_LOGIN=y"}, AUTHOR, "not a plain GitHub login"),
        ({"error": "GitHub rejected the token"}, AUTHOR, "GitHub rejected the token"),
    ],
    ids=["fine-grained", "no-author", "author-any-case", "spaces", "newline", "rejected"],
)
def test_a_token_that_fails_an_identity_check_is_refused_before_repository_access_is_asked(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, object], author: str, message: str
) -> None:
    _identity(monkeypatch, **kwargs)  # type: ignore[arg-type]
    accesses = _access(monkeypatch, "write")
    with pytest.raises(setup_verifier.VerifierRefused, match=message) as raised:
        setup_verifier.verified_bot(BOT_SENTINEL, ENGINE, author)
    assert BOT_SENTINEL not in str(raised.value)
    assert raised.value.__cause__ is None
    assert accesses == []


@pytest.mark.parametrize(
    ("access", "message"),
    [("invited", "has not accepted its invitation"), ("none", "no write access")],
)
def test_a_bot_that_cannot_write_is_refused_with_the_matching_cause(
    monkeypatch: pytest.MonkeyPatch, access: github_repos.RepoAccess, message: str
) -> None:
    _identity(monkeypatch)
    _access(monkeypatch, access)
    with pytest.raises(setup_verifier.VerifierRefused, match=message):
        setup_verifier.verified_bot(BOT_SENTINEL, ENGINE, AUTHOR)


def test_a_repository_that_cannot_be_reached_is_refused_without_a_chained_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _identity(monkeypatch)
    _access(monkeypatch, None)
    with pytest.raises(setup_verifier.VerifierRefused, match="could not be reached") as raised:
        setup_verifier.verified_bot(BOT_SENTINEL, ENGINE, AUTHOR)
    assert raised.value.__cause__ is None


def test_the_body_names_the_reviewer_purpose_and_carries_the_expiry_only_when_there_is_one() -> (
    None
):
    with_expiry = json.loads(setup_verifier.reviewer_body(ENGINE, "tok", date(2099, 6, 30)))
    without = json.loads(setup_verifier.reviewer_body(ENGINE, "tok", None))
    assert with_expiry == {
        "engine": ENGINE,
        "purpose": "reviewer",
        "value": "tok",
        "expires_at": "2099-06-30",
    }
    assert without == {"engine": ENGINE, "purpose": "reviewer", "value": "tok"}


def test_the_expiry_notice_names_the_date_or_says_no_reminder_will_fire(
    capsys: pytest.CaptureFixture[str],
) -> None:
    setup_verifier.print_expiry(date(2099, 6, 30))
    setup_verifier.print_expiry(None)
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == (
        "The token expires on 2099-06-30 (UTC); alan-factory warns you a week before."
    )
    assert "no expiry reminder will fire" in printed[1]


def test_the_verify_run_prints_the_instructions_and_writes_two_private_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _identity(monkeypatch, expires_on=date(2099, 6, 30))
    _access(monkeypatch, "write")
    body, login = _verify(tmp_path)
    printed = capsys.readouterr()
    assert setup_verifier.INSTRUCTIONS_TEXT in printed.out
    assert BOT_SENTINEL not in printed.out + printed.err
    assert json.loads(body.read_text(encoding="utf-8"))["value"] == BOT_SENTINEL
    assert login.read_text(encoding="utf-8") == BOT
    assert {stat.S_IMODE(path.stat().st_mode) for path in (body, login)} == {0o600}


def test_the_verify_run_uses_the_bot_prompt_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import getpass

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    prompts: list[str] = []

    def _fake(prompt: str) -> str:
        prompts.append(prompt)
        return BOT_SENTINEL

    monkeypatch.setattr(getpass, "getpass", _fake)
    _identity(monkeypatch)
    _access(monkeypatch, "write")
    setup_verifier.run_verify(
        ENGINE, AUTHOR, tmp_path / "b", tmp_path / "l", stream=_Tty("")
    )
    assert prompts == [setup_verifier.BOT_PROMPT]


@pytest.mark.parametrize("text", ["", "\n", "  \n"], ids=["eof", "blank", "spaces"])
def test_an_empty_answer_is_refused_before_anything_is_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    seen = _identity(monkeypatch)
    with pytest.raises(setup_transport.TransportRefused, match="no verifier token was entered"):
        _verify(tmp_path, text)
    assert seen == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", f"{KEY}{BOT}\n"),
        ("A=1\n", f"A=1\n{KEY}{BOT}\n"),
        ("A=1", f"A=1\n{KEY}{BOT}\n"),
        (f"A=1\n{KEY}old\nB=2\n", f"A=1\n{KEY}{BOT}\nB=2\n"),
        (f"{KEY}old\r\nB=2\r\n", f"{KEY}{BOT}\r\nB=2\r\n"),
        (f"A=1\n{KEY}x\nB=2\n{KEY}y\nC=3\n", f"A=1\n{KEY}{BOT}\nB=2\nC=3\n"),
        (f"A=1\n{KEY}x\n{KEY}y", f"A=1\n{KEY}{BOT}"),
        (f"# {KEY}old\nA=1\n", f"# {KEY}old\nA=1\n{KEY}{BOT}\n"),
        (f"  {KEY}old\nA=1\n", f"  {KEY}old\nA=1\n{KEY}{BOT}\n"),
        (f"X_{KEY}old\n", f"X_{KEY}old\n{KEY}{BOT}\n"),
        (f"A=1\n\n\nB=2\n{KEY}{BOT}\n", f"A=1\n\n\nB=2\n{KEY}{BOT}\n"),
    ],
    ids=[
        "empty",
        "no-line",
        "no-final-newline",
        "replace-in-place",
        "crlf-kept",
        "duplicates-dropped",
        "duplicate-last-line",
        "comment-untouched",
        "indented-untouched",
        "other-key-untouched",
        "already-correct",
    ],
)
def test_the_rewrite_changes_only_the_login_line(raw: str, expected: str) -> None:
    assert setup_verifier.rewritten(raw, BOT) == expected


@pytest.mark.parametrize(
    ("raw", "recorded"),
    [
        ("", False),
        ("A=1\n", False),
        (f"{KEY}{BOT}\n", True),
        (f"A=1\n{KEY}{BOT}", True),
        (f"{KEY}\n", False),
        (f"{KEY}   \n", False),
        (f"{KEY}{BOT}\n{KEY}other\n", False),
        (f"# {KEY}{BOT}\n", False),
    ],
    ids=["empty", "absent", "present", "no-final-newline", "blank", "spaces", "twice", "comment"],
)
def test_a_login_counts_as_recorded_only_when_exactly_one_non_empty_line_carries_it(
    raw: str, recorded: bool
) -> None:
    assert setup_verifier.login_recorded(raw) is recorded


def test_a_changed_login_line_is_written_atomically_with_mode_600(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("A=1\n", encoding="utf-8")
    secrets.chmod(0o644)
    login = tmp_path / "login"
    login.write_text(BOT + "\n", encoding="utf-8")
    assert setup_verifier.run_login_line(secrets, login) == "changed"
    assert secrets.read_text(encoding="utf-8") == f"A=1\n{KEY}{BOT}\n"
    assert stat.S_IMODE(secrets.stat().st_mode) == 0o600
    assert sorted(path.name for path in tmp_path.iterdir()) == ["login", "secrets.env"]


def test_an_unchanged_login_line_writes_nothing(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text(f"A=1\n{KEY}{BOT}\n", encoding="utf-8")
    secrets.chmod(0o640)
    login = tmp_path / "login"
    login.write_text(BOT, encoding="utf-8")
    before = (secrets.stat().st_mtime_ns, secrets.stat().st_ino, secrets.stat().st_mode)
    assert setup_verifier.run_login_line(secrets, login) == "unchanged"
    after = (secrets.stat().st_mtime_ns, secrets.stat().st_ino, secrets.stat().st_mode)
    assert after == before


def test_a_missing_secrets_file_is_created_holding_only_the_login_line(tmp_path: Path) -> None:
    login = tmp_path / "login"
    login.write_text(BOT, encoding="utf-8")
    secrets = tmp_path / "secrets.env"
    assert setup_verifier.run_login_line(secrets, login) == "changed"
    assert secrets.read_text(encoding="utf-8") == f"{KEY}{BOT}\n"
    assert setup_verifier.run_login_present(secrets) is True


def test_a_symbolic_link_secrets_file_is_refused_and_its_target_kept(tmp_path: Path) -> None:
    target = tmp_path / "target.env"
    target.write_text("A=1\n", encoding="utf-8")
    secrets = tmp_path / "secrets.env"
    secrets.symlink_to(target)
    login = tmp_path / "login"
    login.write_text(BOT, encoding="utf-8")
    with pytest.raises(setup_verifier.VerifierRefused, match="symbolic link"):
        setup_verifier.run_login_line(secrets, login)
    with pytest.raises(setup_verifier.VerifierRefused, match="symbolic link"):
        setup_verifier.run_login_present(secrets)
    assert target.read_text(encoding="utf-8") == "A=1\n"


def test_a_login_file_holding_something_other_than_a_login_is_refused(tmp_path: Path) -> None:
    login = tmp_path / "login"
    login.write_text("a b\n", encoding="utf-8")
    secrets = tmp_path / "secrets.env"
    with pytest.raises(setup_verifier.VerifierRefused, match="not a plain GitHub login"):
        setup_verifier.run_login_line(secrets, login)
    assert not secrets.exists()


def test_the_presence_check_reports_a_missing_file_as_not_recorded(tmp_path: Path) -> None:
    assert setup_verifier.run_login_present(tmp_path / "absent.env") is False


def test_main_verifies_and_writes_the_two_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _identity(monkeypatch)
    _access(monkeypatch, "write")
    monkeypatch.setattr(sys, "stdin", io.StringIO(BOT_SENTINEL + "\n"))
    body, login = tmp_path / "b", tmp_path / "l"
    argv = [
        "verify",
        "--engine",
        ENGINE,
        "--signed-in-user",
        AUTHOR,
        "--body-out",
        str(body),
        "--login-out",
        str(login),
    ]
    assert setup_verifier.main(argv) == 0
    assert BOT_SENTINEL not in capsys.readouterr().out
    assert login.read_text(encoding="utf-8") == BOT


def test_main_prints_only_the_reason_on_a_refusal_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _identity(monkeypatch, fine_grained=True)
    monkeypatch.setattr(sys, "stdin", io.StringIO(BOT_SENTINEL + "\n"))
    argv = [
        "verify",
        "--engine",
        ENGINE,
        "--body-out",
        str(tmp_path / "b"),
        "--login-out",
        str(tmp_path / "l"),
    ]
    assert setup_verifier.main(argv) == 1
    printed = capsys.readouterr()
    assert printed.err.strip() == setup_verifier._FINE_GRAINED_MESSAGE  # noqa: SLF001
    assert BOT_SENTINEL not in printed.out + printed.err
    assert list(tmp_path.iterdir()) == []


def test_main_writes_the_login_line_and_reports_the_outcome(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    login = tmp_path / "login"
    login.write_text(BOT, encoding="utf-8")
    secrets = tmp_path / "secrets.env"
    argv = ["login-line", "--secrets-file", str(secrets), "--login-file", str(login)]
    assert setup_verifier.main(argv) == 0
    assert setup_verifier.main(argv) == 0
    assert capsys.readouterr().out.splitlines() == ["changed", "unchanged"]


def test_main_reports_a_missing_login_file_as_a_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = [
        "login-line",
        "--secrets-file",
        str(tmp_path / "secrets.env"),
        "--login-file",
        str(tmp_path / "absent"),
    ]
    assert setup_verifier.main(argv) == 1
    assert capsys.readouterr().err.strip() != ""


def test_main_answers_the_presence_check_with_its_exit_code(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.env"
    argv = ["login-present", "--secrets-file", str(secrets)]
    assert setup_verifier.main(argv) == 1
    secrets.write_text(f"{KEY}{BOT}\n", encoding="utf-8")
    assert setup_verifier.main(argv) == 0


@pytest.mark.parametrize("argv", [[], ["unknown"], ["verify", "--engine", "a/b"]])
def test_main_exits_2_on_a_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        setup_verifier.main(argv)
    assert raised.value.code == 2


@pytest.mark.parametrize(
    "text",
    [
        setup_verifier.INSTRUCTIONS_TEXT,
        setup_verifier.NO_EXPIRY_TEXT,
        setup_verifier._FINE_GRAINED_MESSAGE,  # noqa: SLF001
        setup_verifier._INVITED_MESSAGE,  # noqa: SLF001
        setup_verifier._NO_ACCESS_MESSAGE,  # noqa: SLF001
    ],
)
def test_the_printed_texts_carry_no_credential_or_identity_pattern(text: str) -> None:
    assert leaks(text) == {}


def test_the_instructions_name_the_role_the_scope_and_why_fine_grained_fails() -> None:
    text = " ".join(setup_verifier.INSTRUCTIONS_TEXT.split())
    assert "Write role" in text
    assert "classic personal access token with the repo scope" in text
    assert "Accept the invitation" in text
    assert "cannot reach a repository owned by another account" in text
    assert "not shown, not printed and not passed on any command line" in text


def test_the_module_reads_no_environment_variable_of_its_own() -> None:
    source = Path(setup_verifier.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source
