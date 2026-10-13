# Claude sign-in

alan-factory's agents run the Claude Code command-line tool, and that tool needs a Claude sign-in on your Mac. There are two ways to give it one: an API key from a Claude Console account, or a login to a Claude subscription account. An API key is recommended: alan-factory's agents run unattended, and an API key is the form Anthropic offers for automated use. A subscription login works the same way technically, but its plan's terms and usage policies govern how it may be used, and checking that they allow how you run alan-factory is up to you.

alan-factory never sees, stores or passes on your Claude credentials. The Claude Code tool reads its own sign-in, and setup never asks you for it. You sign in to Claude Code yourself, as the macOS user alan-factory runs as, and Claude Code keeps that sign-in for that user.

Following Anthropic's terms for the account and plan you use is your responsibility.

## API key

An API key belongs to a [Claude Console](https://platform.claude.com) account. Usage is billed to that account and is bounded by its usage tier and its spend limits.

### Set it up

As the macOS user alan-factory runs as, open a terminal and start `claude`. At the sign-in prompt, choose the Claude Console account and follow the browser steps. Claude Code keeps the resulting credential itself.

### Check it works

From inside your copy of the repository, run:

`scripts/setup_macos.sh --check-claude`

You can also start `claude` as the same macOS user and run `/status`. It shows which sign-in Claude Code is using.

### Troubleshooting

- Runs fail with an authentication error: the key may have been revoked, or the Console organization behind it may be disabled or expired. Sign in again with `/login`, or create a new key in the Console.
- Both an API key and a subscription login are present on the machine: Claude Code uses one of them. `/status` shows which one is in use and marks the other. Remove the one you do not want to use.
- The check passes in your terminal but fails for alan-factory: the sign-in belongs to a different macOS user than the one alan-factory runs as. Sign in again as the user alan-factory runs as.

### Limits

Anthropic places each Console organization on a usage tier. The tier sets request and token rate limits and a monthly spend cap. New organizations can start on lower limits that rise as they build usage history. You can set your own spend limit below the tier's cap on the Billing page of the Console.

Several agents running in parallel all draw on the same organization limits, so a large fleet reaches them sooner than one interactive session. Check your organization's current tier and caps in the Console rather than relying on figures written elsewhere.

## Subscription login

A subscription login belongs to a Claude account on a plan such as Pro, Max, Team or Enterprise. Usage is covered by the plan and is bounded by the plan's usage limits.

### Set it up

As the macOS user alan-factory runs as, open a terminal and start `claude`. At the sign-in prompt, choose the Claude subscription account and follow the browser steps. Claude Code keeps the resulting credential itself.

### Check it works

From inside your copy of the repository, run:

`scripts/setup_macos.sh --check-claude`

You can also start `claude` as the same macOS user and run `/status`. It shows which sign-in Claude Code is using.

### Troubleshooting

- Runs fail with an authentication error: the login may have expired and could not be refreshed. `/status` can show the login as expired. Sign in again with `/login`.
- Both a subscription login and an API key are present on the machine: Claude Code uses one of them. `/status` shows which one is in use and marks the other. Remove the one you do not want to use.
- The check passes in your terminal but fails for alan-factory: the sign-in belongs to a different macOS user than the one alan-factory runs as. Sign in again as the user alan-factory runs as.

### Limits

A plan's usage limits are shared between Claude and Claude Code, and they reset on a schedule Anthropic sets. Several agents running in parallel all draw on the same plan allowance, so a large fleet uses it up sooner than one interactive session. Check your plan's current limits with Anthropic rather than relying on figures written elsewhere.

When a plan's limit is reached, Anthropic refuses work until the limit lifts. See the next section for what alan-factory does.

## When sign-in fails or a limit is reached

**A lapsed sign-in or a revoked key.** Runs fail with an authentication error. Nothing pauses: alan-factory keeps starting work, and each run that starts while the sign-in is broken fails. Fix the sign-in promptly, using the troubleshooting steps for your method above.

**A spend limit.** Anthropic refuses work when an organization reaches its tier's monthly spend cap, or a spend limit you set yourself. alan-factory recognises that response and stops all work. It does not start again by itself, and the regain date Anthropic states is only informational. The queue stays stopped until you press **Resume queue** on the **Queue stopped** banner.

- The banner names the cause and the fix: raise the spend limit in the Console, or wait until access returns, then press Resume.
- The stop survives a restart of alan-factory.
- The job that hit the limit is put back in the queue, not failed, and its workspace stays active.
- If the limit still applies after you resume, the next job that runs trips the stop again.

How alan-factory treats the extra-usage limit of a paid subscription plan has not been verified. If the queue stops and you use a subscription login, check your plan's usage in your Anthropic account before you press **Resume queue**.

**An ordinary usage or session limit.** alan-factory pauses for the time Anthropic reports, plus a short buffer, and then continues by itself. The interrupted job is queued again, not failed, and nobody needs to press anything.
