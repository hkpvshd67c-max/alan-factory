# Verifier bot

alan-factory opens pull requests on your copy of the repository, and a second GitHub account reviews them. That second account is the verifier bot. This page explains why it exists, how to create it, how to give alan-factory its token, and what alan-factory does when the token is missing, expired or wrong.

## Why alan-factory needs a second account

alan-factory's agents open pull requests as the GitHub account you signed in with. GitHub does not accept a review of a pull request from the account that authored it, so a review posted by that same account is refused as a self-review. A separate account has to post the review.

The bot gives alan-factory's automated reviewer its own attributable identity. A review it posts is an automated review, not a human one.

GitHub's [Terms of Service, section B (Account Terms)](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#b-account-terms) describe a machine account as an account that a human sets up and accepts the terms for, using a valid email address, and for whose actions that human is responsible. The same section limits how many free machine accounts one person can keep next to a personal account. Decide whether this use fits those terms before you create the bot, because you are the person responsible for what the bot does.

## Invite the bot as a collaborator

1. Create a new GitHub account for the bot. Use an email address you control, for example an address at `example.com`.
2. On GitHub, open the collaborator settings of `<owner>/<repo>` and invite the bot's username with push access. On a repository owned by an organization, that is the Write role.
3. Sign in to GitHub as the bot and accept the invitation. The invitation email links to it, and it also lives at `https://github.com/<owner>/<repo>/invitations`.

Until the bot accepts, the invitation is pending and the bot cannot review anything.

## Create the token

Sign in as the bot and create a classic personal access token at [github.com/settings/tokens](https://github.com/settings/tokens).

- Select the `public_repo` scope if your copy is a public repository.
- Select the `repo` scope instead if your copy is private.
- Give the token an expiry date. Setup reads it from GitHub, so you type no date.

Do not use a fine-grained token: GitHub's own list of fine-grained token limitations includes contributing to repositories where the user is only a collaborator, which is exactly the bot's position on `<owner>/<repo>` when another account owns it. GitHub's page on [managing personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) explains both token types.

Treat the token like a password. Do not paste it into a chat, an issue or a file in the repository.

## Hand the token to alan-factory

Setup asks for the token in the full first run, `./setup.sh`, or later on its own, from inside your copy of the repository:

`scripts/setup_macos.sh --only verifier`

alan-factory keeps the token in its own secrets store, on the Mac it runs on, and never shows the value again. Setup reads the token's expiry date from GitHub, and that date is what enables the expiry warning described below. A token with no expiry date works, but alan-factory cannot warn you before it lapses.

## When the credential is not ready

A copy whose profile requires a reviewer credential, which is the profile setup writes, counts as not ready in any of these cases:

- **Missing.** No verifier token has been recorded with alan-factory. A token that only exists in the machine's own GitHub sign-in does not count.
- **Expired.** Today is on or after the token's expiry date.
- **Same account as the author.** A check has found that the bot and the account that opens the pull requests are the same GitHub account. alan-factory makes that comparison when it checks its connection to your repository, so a copy is only reported this way after that check has run.

While the copy is not ready, alan-factory dispatches nothing:

- Creating a workspace is refused.
- A manual dispatch is refused.
- Answering a question an agent asked is refused, and the question stays pending.

Each refusal names the repository, the requirement (`reviewer_credential`) and what to do about it. Follow the fix it names, then repeat the action.

Work that was already queued is not lost. Queued jobs wait, and they resume by themselves shortly after the credential holds again. You do not need to re-queue them.

## The expiry warning

When the token has an expiry date, alan-factory shows a warning in its health report starting 14 days before that date and ending the day before it. The warning names the date. It never blocks anything: work continues normally while the warning is showing.

On the expiry date itself the credential counts as expired and the copy is not ready. If the token has no expiry date there is no warning, and the first sign of a lapsed token is a failed review.

## Rotate the token

1. Sign in as the bot and create a new classic token with the same scope and an expiry date.
2. In alan-factory, open Settings → Credentials and delete the stored reviewer credential.
3. From inside your copy of the repository, run `scripts/setup_macos.sh --only verifier` and paste the new token.
4. Revoke the old token at [github.com/settings/tokens](https://github.com/settings/tokens) while signed in as the bot.

The copy is not ready between step 2 and step 3, because setup reports the verifier step as already configured for as long as a credential is stored. Queued work waits during that time and resumes by itself once the new token is stored; see [When the credential is not ready](#when-the-credential-is-not-ready).

## Remove the bot

1. In alan-factory, open Settings → Credentials and delete the stored reviewer credential. This is not a setup command.
2. Revoke the token at [github.com/settings/tokens](https://github.com/settings/tokens) while signed in as the bot.
3. Remove the bot's collaborator access from `<owner>/<repo>`.

After the stored credential is deleted, a copy whose profile requires a reviewer credential is not ready and dispatches nothing until a new token is stored.

## What the token can reach

See [Security](security.md) for what the verifier token can reach and why the bot should own nothing and collaborate only on this copy.
