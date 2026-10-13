# SonarCloud

SonarCloud reviews the code in pull requests on your copy of the repository and reports findings. This page explains that it is optional, how to connect it, what it does and does not do to merges, and what happens to the `gate` check when it is connected.

## SonarCloud is optional

A copy is ready, dispatches work and passes `gate` without SonarCloud. Nothing in alan-factory requires it.

- To skip it, press Enter at the admin token prompt during setup. Pass `--no-sonar` to skip it without being asked: `./setup.sh --no-sonar`.
- To connect it later, run this from inside your copy of the repository: `scripts/setup_macos.sh --only sonar`.
- `--no-sonar` changes nothing on a copy that is already connected.

## Connect it

Setup asks for these in this order. Do the browser steps first, then paste the tokens when setup asks.

1. Sign in at [sonarcloud.io](https://sonarcloud.io) (now named SonarQube Cloud) with GitHub. Choose **+** (top right) > Analyze new project, pick your organisation (create one first if you have none), tick `<owner>/<repo>` and choose Set Up. Import the repository once: setup refuses several projects bound to one repository.
2. Create an admin token: your avatar > My account > Access Tokens > Personal Tokens > Generate Token, as a user who administers the organisation. Setup uses it only on this Mac, to find your project and read its analysis settings. Setup never stores it.
3. Create an analysis token the same way, as a different SonarCloud user than the admin token's. Setup stores it as a GitHub Actions secret of your copy, so CI can send analyses. A token from the same user is refused unless you accept the stated risk: that token can do everything its user can, including administering the organisation, and every workflow run in your copy can use it. To accept it, run `scripts/setup_macos.sh --only sonar --accept-sonar-user-token`.
4. Paste the admin token first, then the analysis token. Neither is shown, printed or passed on a command line.
5. If setup says SonarCloud did not confirm that Automatic Analysis is off, open the project on sonarcloud.io, choose Administration > Analysis Method and turn Automatic Analysis off. Setup carries on without waiting for it.

## SonarCloud is advisory

SonarCloud's findings and its quality gate never block a merge. The Free plan allows no custom quality gate, and the default quality gate needs a person to review each new security hotspot. Review the findings yourself; see [Review hotspots](security.md#review-hotspots).

## What `gate` needs when SonarCloud is connected

On a connected copy, the `gate` check needs the SonarCloud analysis job to succeed. `gate` turns red when:

- the analysis token has been revoked;
- Automatic Analysis is still on for the project;
- SonarCloud's plan will not analyse the project.

A copy that is not connected runs no analysis, and `gate` passes.

## Private copies

Setup creates your copy as a private repository. A connected private copy may not be analysed on SonarCloud's Free plan. When SonarCloud will not analyse it, the analysis job fails and `gate` turns red. Weigh that before you connect a private copy.

## Setup's wait

Near the end, setup waits for SonarCloud to report on a connected copy. If SonarCloud does not report within setup's wait, that is a warning and setup still completes. Setup does not prove that SonarCloud reports on your main branch.
