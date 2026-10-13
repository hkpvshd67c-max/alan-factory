# Security

alan-factory runs autonomous agents that write code, run shell commands and merge pull requests on your copy of the repository. This page covers the choices that limit what could go wrong: which macOS user runs alan-factory, what you spend, what each token can reach, and how to review security hotspots.

## Use a dedicated macOS user

Run alan-factory as a macOS user that exists only for alan-factory.

- **Any local account or process that can reach the port can read the API and can also create, dispatch, stop and delete work.** alan-factory serves its API on the loopback address of the Mac, and any account on that Mac can connect to it. Most of those actions need no control token (see [What the control token protects](#what-the-control-token-protects)). The work they start runs agents with shell access as the user alan-factory runs as. alan-factory checks the Host header of every request and refuses foreign hostnames, which protects against a web page pointing its own hostname at loopback. That check is not authentication, because a client that is not a browser can send any Host header it likes. alan-factory also refuses a request other than GET, HEAD or OPTIONS when it comes from a page on another site (see [Cross-site requests](#cross-site-requests)).
- **Agents run shell commands as that user.** Everything the user alan-factory runs as can read, an agent can read. That includes that user's files, its other sign-ins and any tokens stored for alan-factory. alan-factory keeps its stored tokens in files that only that user can read, but the agents run as that same user.

Running alan-factory as a dedicated macOS user is the mitigation. It does not stop another local account from connecting to the port, but it limits what anything that reaches the port can do: the agents it dispatches run as that user, so your personal files, your browser sessions and your other credentials stay out of their reach. Sign in to Claude Code, and to GitHub, as that user. See [Claude sign-in](claude-sign-in.md).

1. The dedicated user is **a separate account, not your main one**.
2. If Homebrew on this Mac belongs to another account, sign in as **the account that owns Homebrew** and run `brew install git gh uv node@22 gitleaks pre-commit tmux` first. Setup can't install into a Homebrew it can't write to, and refuses with this command when something is missing.
3. Put that Homebrew on the dedicated user's PATH: add the line ending in `brew shellenv` that Homebrew's installer printed to the dedicated user's shell profile (for example `~/.zprofile`), open a new terminal window, then run setup.

## What the control token protects

The control token is a secret that alan-factory's own screens send with the actions below. A request without it is refused. The groups are described by category, not as a closed list: other actions can need the token too.

These need the control token:

- repository and engine administration;
- secrets;
- the control token itself;
- the operator terminal;
- onboarding;
- a few workspace actions, such as answering a question or attaching a pull request;
- others, such as the driver loops and resuming the queue.

These need no control token:

- workspace create, dispatch, complete, delete, unarchive, notes, stop, resume and archive;
- build chat and explore chat sessions;
- project create, patch, complete, delete and amendments;
- the workspace reindex.

So a local process that is not a browser, or a page served from a loopback hostname on any port, can still start agents, stop them and delete their work without the token.

alan-factory refuses to start on an address that is not loopback: it accepts only an address in `127.0.0.0/8`, `::1` or the name `localhost`, and refuses any other hostname, even one that your hosts file maps to loopback. That guard covers the address alan-factory itself binds. It does not cover forwarding. Forwarding the loopback port, with an SSH tunnel or a tailnet or reverse-proxy publish, gives every client of that forward the same unauthenticated read-and-dispatch access, and the bind guard does not stop this. Forward the port only to people you would trust with a shell as the user alan-factory runs as.

## Cross-site requests

A web page you have open in your browser can send a request to the loopback address of your Mac. Without a check, a page on any site could use that to start or delete work on your behalf.

alan-factory refuses a request that changes something, which means any method other than GET, HEAD or OPTIONS, when the request comes from a page on another site:

- When the browser sends an `Origin` header, its hostname must be a loopback name or the hostname of the preparation chat origin you configured. The port is ignored. A missing, empty, `null`, repeated or malformed value is refused.
- When the browser sends no `Origin` header but sends `Sec-Fetch-Site`, the value must be `same-origin` or `none`.
- A request that carries neither header is served, because a client that is not a browser sends neither.

A refused request gets `403` with `origin not allowed`.

This has limits:

- A local process that is not a browser still needs no token for the routes listed under "These need no control token". It sends neither header, so alan-factory serves it.
- Any page served from a loopback hostname on any port passes the Origin check, because the port is ignored.
- A page on another site cannot read the response to a request it sends to alan-factory, and alan-factory also refuses the write itself, so the page cannot make alan-factory act by sending it.

## Prompt injection

Agents read text that other people write, and that text can contain instructions aimed at the agent. The inputs to treat as untrusted are:

- issue bodies;
- pull request and review comments, including the comments of Copilot;
- Dependabot pull requests;
- pages that the research roles fetch.

The implementation roles run shell commands with no operating-system sandbox. The credential deny rules alan-factory gives them are prefix matches: they block the commands they name, not every command that could reach the same credential. Treat them as a speed bump, not a wall.

These defences limit the damage:

- Setup configures your copy with the posture `propose`. At that posture alan-factory admits the teams that research, plan and implement, and does not admit the teams that verify and merge until you raise it. The default in the code, when no posture is set, is `observe`, which admits none of them.
- Interaction limits restrict who can open issues, pull requests and comments on a public copy to its collaborators. They apply on a public copy only, last at most six months, and are not renewed automatically. Re-running setup applies them again.
- A dedicated macOS user keeps your personal files and sign-ins out of an agent's reach (see [Use a dedicated macOS user](#use-a-dedicated-macos-user)).
- A private repository keeps strangers from filing issues and pull requests at all.

## Set an Anthropic spend limit

Agents run unattended, so set a limit in your Anthropic account before you start a large fleet.

- With an API key, open the Billing page in the settings of the [Claude Console](https://platform.claude.com) and set a spend limit below your usage tier's cap. When the limit is reached, Anthropic refuses work and alan-factory stops all work until you press **Resume queue**.
- With a subscription login, the plan's usage limits bound what agents can use. Check your plan's current limits in your Anthropic account.

[Claude sign-in](claude-sign-in.md) describes what alan-factory does when a limit is reached.

## What the verifier token can reach

The verifier token is a classic personal access token for a second GitHub account, the bot that reviews pull requests. See [Verifier bot](verifier-bot.md) for how to create it.

A classic token with the `public_repo` scope can write to every public repository the bot account can write to, not only your copy. The `repo` scope you use for a private copy is broader still: it covers every private repository the bot can reach. The token does not know which repository alan-factory uses it on.

So keep the bot's reach small on purpose:

- The bot owns nothing.
- The bot collaborates only on this copy, `<owner>/<repo>`.
- Do not invite the bot to other repositories, and do not reuse the account for anything else.

Give the token an expiry date. Setup reads it from GitHub, and alan-factory warns you 14 days before it lapses. Rotate the token as described in [Verifier bot](verifier-bot.md). If you suspect the token leaked, revoke it at [github.com/settings/tokens](https://github.com/settings/tokens) while signed in as the bot.

## What the transport token can reach

alan-factory uses the transport token for git operations against your copy and for its GitHub API calls on that copy.

The token is a fine-grained personal access token scoped to `<owner>/<repo>` only. That is the scope of the token you create, not something alan-factory enforces: alan-factory uses whatever token it is given.

The permissions setup prints for the token:

```
Administration: Read and write
Contents: Read and write
Pull requests: Read and write
Issues: Read and write
Actions: Read and write
Workflows: Read and write
Commit statuses: Read-only
Metadata: Read-only (GitHub adds it by itself)
```

Every other permission stays at No access.

The Administration write permission lets alan-factory apply branch protection and change repository settings, not only clone, fetch and push. No other permission allows that, and without it the protect step of setup fails.

Your existing GitHub CLI sign-in usually covers every repository your account can reach, so alan-factory uses it only if you opt in. In the first run, opt in with:

`./setup.sh --use-gh-token`

On a copy that has no transport credential stored yet, opt in from inside the copy with:

`scripts/setup_macos.sh --only credential --use-gh-token`

The flag applies only while no transport credential is stored.

## Review hotspots

SonarCloud is optional, and its findings never block a merge when it is connected. See [SonarCloud](sonarcloud.md) for how to connect it. A pull request can merge with SonarCloud findings open, so review them yourself if you connected it.

- Review security hotspots in SonarCloud.
- A hotspot that alan-factory marked "Acknowledged" was not reviewed by a person. alan-factory does this only on a merge-recovery path, when a merge is held up by a SonarCloud result whose only findings are hotspots, and only when the environment alan-factory runs in has a SonarCloud token.
- Without that token alan-factory does not mark any hotspot.

Treat an "Acknowledged" hotspot as unreviewed until you have looked at it yourself.
