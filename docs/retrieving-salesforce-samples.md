# Retrieving a Salesforce sample — locally, no token leaves your machine

The agent never connects to Salesforce (it's network-isolated by design — see the
read-only safety principle in `ARCHITECTURE.md` §1.1). For testing the
digest we need the **metadata source files** (`force-app/`), which you retrieve on
your own machine with the Salesforce CLI. Auth stays local; you never copy a token.

## 0. Install the Salesforce CLI (if needed)

```bash
npm install -g @salesforce/cli
sf --version
```

## 1. Log in (browser login — nothing to paste)

```bash
sf org login web --alias sample
```

This opens a browser; the CLI stores a refresh token **on your machine**. No token
is ever shown to or handled by the agent.

## 2. Make a project shell (gives the retrieve somewhere to land)

```bash
sf project generate --name sf-sample
cd sf-sample
```

## 3a. Retrieve a focused set (recommended for parser testing)

```bash
sf project retrieve start --target-org sample \
  --metadata ApexClass ApexTrigger CustomObject Flow LightningComponentBundle PermissionSet
```

## 3b. …or retrieve everything via a manifest

```bash
sf project generate manifest --from-org sample --name package --output-dir manifest
sf project retrieve start --target-org sample --manifest manifest/package.xml
```

Either way, source lands under `force-app/main/default/` (`classes/`, `triggers/`,
`objects/`, `flows/`, …).

## 4. Hand it to the agent without touching git

Drop the retrieved tree into this repo's gitignored `samples/` area:

```bash
cp -R force-app  <repo>/samples/sf/        # samples/ is gitignored
# or zip it (the *.zip glob is gitignored too):
# cd sf-sample && zip -r <repo>/samples/sf-sample.zip force-app
```

Nothing under `samples/` and no `*.zip` is ever committed — verified with
`git check-ignore`.

## Why no token?

- The agent is network-isolated and **must not** hold a live Salesforce token.
- A Salesforce access token can read *and write* org data — it should never enter a
  chat, a repo, or the agent. The CLI keeps auth on your machine; we only ever
  handle the exported metadata files.
- This is the same principle as the Jira/Confluence scraper: credentials stay with
  you; only read-only *exports* come to the agent.
