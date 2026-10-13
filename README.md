# alan-factory

## What alan-factory is

alan-factory is an autonomous multi-agent engineering system. It is a single-process FastAPI service with a React UI. Headless `claude` workers plan, implement, verify, review and merge pull requests against your own repository.

## Getting started

### Before you start

- An Apple-silicon Mac with at least 16 GB of RAM and 20 GB of free disk, signed in at the desktop. Setup checks the architecture, the memory and the free disk, and refuses on anything less.
- Xcode Command Line Tools, which provide `git`.
- A GitHub account, and a second GitHub account for the verifier bot.
- Claude Code access. An Anthropic API key is recommended for alan-factory's unattended use; a subscription login also works, under its own plan terms (see the safety notice).
- A macOS user that exists only for alan-factory. This is recommended, not enforced; [Security](docs/tutorials/security.md) explains why.

Linux and Windows are not supported. Running alan-factory in Docker may follow in a later release; the [Docker roadmap](docs/roadmap/docker.md) describes the idea and promises nothing until an item ships.

### Install

1. Sign in to GitHub in your browser, open this repository and click **Fork**. Keep the name `alan-factory` and keep the fork public: GitHub Free gives branch protection only to public repositories, and alan-factory's merge gate needs it.
2. In your fork, open the **Actions** tab and click **I understand my workflows, go ahead and enable them**. A fork starts with its workflows off; setup checks this and stops with the same instruction if you skip it.
3. Clone your fork to `~/alan-factory` and run setup there:

```
git clone https://github.com/<you>/alan-factory.git ~/alan-factory
cd ~/alan-factory
./setup.sh
```

Use plain `git clone`: `gh repo clone` adds an `upstream` remote that points back at this repository. Setup finds your fork already cloned at `~/alan-factory`, installs alan-factory's service from there and starts it. It also turns on Issues for your fork, which a fork starts without.

SonarCloud is optional. Skip it with `./setup.sh --no-sonar`, or press Enter at its first prompt.

#### Private copy (needs GitHub Pro)

To keep your copy private, clone this repository anywhere except `~/alan-factory` (for example `git clone <template-url> ~/alan-template`) and run `./setup.sh` from that checkout. Setup then creates a private repository named `alan-factory`, owned by your GitHub account (or by an organisation, with `./setup.sh --owner <org>`), clones it to `~/alan-factory` and continues from there. The template checkout is no longer needed afterwards. A private repository has branch protection only on a paid GitHub plan (Pro, Team or Enterprise); on GitHub Free the `protect` step fails.

Do not use GitHub's "Use this template" button. If you already made a copy that way, under any name, clone it and run `scripts/setup_macos.sh` inside that clone instead.

### What only you do

Setup prints this list before it starts and tells you when each step is needed.

1. Sign in to GitHub in your browser when `gh auth login` opens it (about 2 minutes). If you forked, turn on your fork's workflows in its Actions tab first (step 2 of Install).
2. Create a fine-grained GitHub token for your repository, in your browser (about 3 minutes). Setup prints the exact permissions when it asks for the token; [Security](docs/tutorials/security.md) lists them.
3. Approve the GitHub apps alan-factory needs on your repository (about 5 minutes).
4. Optional: import your repository into SonarCloud and create two SonarCloud tokens (about 10 minutes). See [SonarCloud](docs/tutorials/sonarcloud.md).
5. Create the verifier bot's separate GitHub account, invite it to your repository with write access, accept the invitation as the bot and create a classic token for it (about 10 minutes). See [Verifier bot](docs/tutorials/verifier-bot.md).
6. Sign in to Claude yourself: run `claude` once in a terminal and follow its own prompt. Setup never signs in for you. See [Claude sign-in](docs/tutorials/claude-sign-in.md).

### How long it takes

Setup's own time: plan on 1 to 2 hours in total. Setup prints the same estimate.

Your first merged pull request takes longer than setup. A task has to be planned, implemented, verified, reviewed and run through CI, and at posture `propose` the merge waits for you.

### Opening the web UI

Once setup is complete, the web UI is at `http://127.0.0.1:8585`. You can look around without a token, but making changes needs the control token. Setup never prints the token. In a terminal, as the macOS user that runs alan-factory, copy it to the clipboard:

```
pbcopy < ~/.alan/state/control_token
```

In the UI, click the gear icon (its accessible label is **Global settings**), paste the token into **Control token** and click **Save**.

### If setup stops

With a fork, run `./setup.sh` or the resume command setup prints from inside `~/alan-factory`. With a private copy, if setup stops before it has created your copy, run `./setup.sh` again from the template checkout; once your copy exists, setup prints a resume command: run it from inside `~/alan-factory`.

- A `copy-destination` refusal means something already sits at `~/alan-factory`. Move it away and run setup again.
- A `template-clone` refusal means `scripts/setup_macos.sh` was run inside the template checkout. Use `./setup.sh` there instead.
- A `copy-is-template` refusal means gh is signed in as the account that owns this repository, so your copy would be the template itself. Sign in to gh as a different account, or pass `--owner <org>`.
- A `harden` failure naming GitHub Actions workflows means your fork's workflows are still off. Turn them on in its Actions tab and run the resume command.

### Tutorials

- [Verifier bot](docs/tutorials/verifier-bot.md): create the second account and its token.
- [Claude sign-in](docs/tutorials/claude-sign-in.md): sign in to Claude Code as the user that runs alan-factory.
- [Security](docs/tutorials/security.md): the dedicated macOS user, spend limits and what each token can reach.
- [SonarCloud](docs/tutorials/sonarcloud.md): the optional code-quality connection.

## Safety notice

alan-factory is an experimental prototype that runs unattended. Once it is set up, its agents run the Claude Code CLI on your Mac: they edit code, run shell commands, and open and merge pull requests in your own copy of this repository. The pull requests and commits they make are AI-generated.

- The licence is MIT, and the software comes with no warranty.
- A separate verifier machine account is mandatory. It is a second GitHub account, distinct from the account that owns your copy.
- An Anthropic API key is the recommended way to run alan-factory's unattended agents. A Claude subscription plan is governed by its applicable Anthropic terms and usage policies, which may restrict automated or continuous use. Checking that your plan allows how you run alan-factory is your responsibility.
- SonarCloud is optional. Setup lets you skip it, and the required `gate` check passes without it. If you connect it, its findings never block a merge.
- A new copy starts at posture `propose`, so every merge waits for you. If you raise the posture so that agents merge on their own, pull requests reach `main` with no human review, and alan-factory's updater then applies them from `main`, restarting the service when a change needs it.
- Both recurring sweeps start off: the "Failure triage sweep" (`triage_sweep`) and "Alan's dependency sweep" (`dependency_sweep`). Leave them off until your engine is set up and working. They are turned on by setting `triage_sweep` and `dependency_sweep` to `true` in the installed default profile; nothing here promises a toggle for them in the web UI.
- Implementation roles can still read files through scripts they run, and alan-factory provides no sandbox. Run alan-factory under a dedicated macOS user that owns nothing else. An operating-system sandbox is not provided.
- When the operator terminal's prerequisites are installed, the terminal holds your full authority. The control token plus network reach to the service is equivalent to a shell on your machine.
- Use alan-factory only on repositories you own, and not on production systems.
- You alone are responsible for the code merged into your repository, for every model, GitHub Actions and SonarCloud cost it incurs, and for following Anthropic's and GitHub's terms, including for the verifier bot account.

### With the triage sweep off

Health findings are never computed while `triage_sweep` is off. As a result the self-updater's blocked or rolled-back finding is invisible, and the "Daily digest" row's chip shows a false "Overdue". This is a known limitation.

## Costs

- `dependency_sweep`, when on: it hands Dependabot pull requests that need a rebase or a fix to an implementation worker, which spends model usage (API tokens or subscription quota), and what the worker pushes runs CI, which spends GitHub Actions minutes. It also enables the Python vulnerability scan, which runs `pip-audit` and opens an issue for each fixable finding.
- `triage_sweep`, when on: it spends no model usage, because it is a deterministic health check.
- A private copy pays for GitHub Actions minutes and may hit SonarCloud's Free-plan limits.

## No support

alan-factory is a prototype. There is no support channel, no maintenance commitment and no commitment to respond, and no fix or further release is promised. Further work, such as Docker support, happens only at the maintainer's discretion. Issues and pull requests may go unanswered.

Forks are welcome under the MIT licence. A fork is independent of this repository, and whoever maintains it is responsible for it.

## No affiliation

alan-factory is not affiliated with or endorsed by Anthropic, GitHub or SonarSource.
