# RocketRide staging setup

Installed the official `rocketride.rocketride` VS Code staging extension **1.2.0**
from https://staging.rocketride.ai/client/vscode. Project VS Code settings point
both development and deployment to https://staging.rocketride.ai.

The workspace uses the **1.3.0 SDK bundled inside that staging VSIX** through
`vendor/rocketride-client.tgz`. It includes the `login`, `init`, and app lifecycle
commands needed by the supplied hackathon guide. The public npm 1.3.0 artifact
failed its CLI help check (`process.on is not a function`) and was replaced by
the staging artifact. The installed staging CLI help passes.

## Finish connection

The staging CLI connection is now authenticated as the user-authorized account.
`init` has completed, including the service catalog, schemas, and platform packages.
The commands below refresh the connection or verify it again.

Run from the workspace root:

```sh
pnpm exec rocketride init --uri https://staging.rocketride.ai
pnpm check:rocketride
```

Complete the browser login with your chosen account. `init` saves credentials in
the ignored `.env`, retrieves the service catalog and schemas, vendors matching
platform packages, and installs agent docs. It can replace the initial SDK
dependency with `.rocketride/client/rocketride.tgz`; this is expected.

`check:rocketride` makes a public server probe and, if configured, an authenticated
connection. It does not start a pipeline or spend pipeline credits. Exit 2 means
the server is reachable but credentials are missing; exit 1 means a check failed.

The separate `pnpm check:rocketride:pipeline` script validates and, when credits
exist, runs `echo.pipe`. Staging validation passed with zero warnings. The
execution check stopped because there was no positive compute credit balance.
It did not launch a task or buy credits. Supply the event promo code to finish
the run check.

In VS Code open this workspace, then **RocketRide: Settings**, verify Cloud and
the custom staging URL, sign in, and **Save**. CLI login and the extension's OAuth
session are separate; a successful CLI check alone does not verify IDE sign-in.

## Before building a real app

1. Redeem the event's promo code on the staging launcher (code not supplied yet).
2. Create a scratch app, then claim the team's developer ID. The ID must contain
   letters and underscores only. No developer ID was invented or registered.
3. Create the actual app after claiming the namespace.
4. Read `.rocketride/docs/ROCKETRIDE_README.md`, `services-catalog.json`, and `schema/`.
5. Keep strict type checking enabled and add a real pipeline smoke test.
6. Publish to `@me` or `@team`; close the App Builder watch before verifying the
   deployed version on staging.

Sources: the user-supplied staging guide, the downloaded extension manifest and
bundled SDK, and https://docs.rocketride.org/ide-extensions/vscode/usage/.
