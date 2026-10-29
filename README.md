# appsignal-gateway-operator

Keeps [AppSignal](https://www.appsignal.com) uptime monitors in sync with the hostnames
your cluster actually serves.

It watches Gateway API `HTTPRoute` resources, works out which hostnames deserve a
monitor, and makes AppSignal agree: creating what appeared, updating what changed,
removing what is gone.

```
created monitor 4f2a… for https://shop.example.com/ (shop.example.com)
deleted monitor 9c1e… for https://old.example.com/ — no route serves it any more
```

> Not affiliated with AppSignal.

## How it decides what to monitor

Every hostname on every `HTTPRoute` is a candidate.

**By default, it is monitored.** A production hostname is covered the moment a route
serves it.

**Unless it matches `EXCLUDE_SUFFIXES`.** That is where development and staging domains
go. With `EXCLUDE_SUFFIXES=dev.example.com,test.example.com`, nothing on those domains is monitored.

**Unless the route says otherwise:**

```yaml
metadata:
  annotations:
    appsignal-gateway-operator/enabled: "true"    # monitor it even on an excluded domain
    appsignal-gateway-operator/enabled: "false"   # never monitor it, excluded or not
```

`"false"` applies to the hostname, not just the route. A hostname is usually served by
two routes, the real one and a `:80` companion that redirects. Annotating either one is
enough. When in doubt, `"false"` wins.

**Redirect routes** are ignored where a real route serves the same hostname, so one
hostname means one monitor. A hostname served *only* by a redirect still gets one.

**Wildcards** (`*.example.com`) are never monitored. There is no address to request.

## Ownership: one app, one marker

AppSignal has no monitor groups. An uptime monitor belongs to an app, so the operator
writes into the one app you name with `APPSIGNAL_APP_ID`. Inside that app, a monitor is
its own when the **first line of its description** is

```
managed by appsignal-gateway-operator, cluster <CLUSTER_NAME>
```

Everything follows from that line.

- **A monitor without it is never touched.** Anything you make by hand in the same
  app is invisible to the operator.
- **You may add text below it.** A runbook link on the second line is kept across
  updates.
- **Editing or removing the first line disowns the monitor.** The operator then creates
  a new one for that URL on the next pass, and the old one stays where it is. That is
  the way to take a monitor out of its hands.
- **Two clusters must never share a `CLUSTER_NAME`.** They would each read the other's
  monitors as their own and delete them as unserved, every resync. The operator refuses
  to start without a name but cannot tell that one is already in use elsewhere.

Give each cluster its own AppSignal app. Two clusters in one app work, because the
marker keeps them apart, but the monitor list then mixes them.

The operator needs no write access to anything in Kubernetes. Its `ClusterRole` is
read-only: `httproutes`, plus the `customresourcedefinitions` and `namespaces` that
kopf reads to resolve what it is watching.

## Installing

```bash
kubectl apply -f https://raw.githubusercontent.com/SevenLabnl/appsignal-gateway-operator/v0.1.0/deploy/operator.yaml
```

Then the cluster name, the app id and the token. See [`deploy/`](./deploy) for the full
walkthrough. It ships with `DRY_RUN: "true"`. Read the plan it logs, then turn it off.

### The token

An AppSignal **personal API token**, from *Personal settings → API tokens*. The GraphQL
API accepts nothing narrower. See [SECURITY.md](./SECURITY.md) for what that means.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `APPSIGNAL_TOKEN` | — | **required** |
| `APPSIGNAL_APP_ID` | — | **required**, the app the monitors live in |
| `CLUSTER_NAME` | — | **required**, goes into the ownership marker; must be unique per cluster |
| `EXCLUDE_SUFFIXES` | `""` | comma-separated hostname suffixes never monitored |
| `REGIONS` | `europe` | comma-separated, from `europe,north-america,asia-pacific,south-america` |
| `WARMUP_MINUTES` | `0` | minutes a check must keep failing before an alert opens |
| `SSL_EXPIRATION` | — | `true` or `false`: alert before the certificate expires. Unset leaves the field alone |
| `NOTIFIER_IDS` | — | comma-separated notifier ids for monitors it creates; see below |
| `RESYNC_SECONDS` | `900` | full reconcile interval |
| `DRY_RUN` | `false` | log what would change, change nothing |

Bad values are rejected at startup, so a typo in a ConfigMap fails visibly instead of
quietly monitoring nothing.

## Annotations

| Annotation | Effect |
|---|---|
| `appsignal-gateway-operator/enabled` | `"true"` monitors the hostname even on an excluded domain, `"false"` never monitors it |
| `appsignal-gateway-operator/path` | path to request instead of `/` |
| `appsignal-gateway-operator/check-body-content` | text the response body must contain |
| `appsignal-gateway-operator/warmup` | minutes, overrides `WARMUP_MINUTES` |

### Checking the response body

A page can answer 200 and still be broken: an empty shell, a maintenance page, a proxy's
error page. Require text that only a working response contains:

```yaml
metadata:
  annotations:
    appsignal-gateway-operator/path: /api/health
    appsignal-gateway-operator/check-body-content: '"status":"ok"'
```

AppSignal then fails the check when the body does not contain that text. Remove the
annotation to drop the check again; the next reconcile clears it.

### Waiting before alerting

`warmup` is how many minutes a check must keep failing before AppSignal opens an alert.
Raise it for a service that is slow to come back after a deploy:

```yaml
metadata:
  annotations:
    appsignal-gateway-operator/warmup: "10"
```

It must be a whole number of minutes. Any other value is ignored with a warning in the
operator log, and `WARMUP_MINUTES` applies.

### Pointing at a health endpoint

With AppSignal APM on the same app, every uptime check is a request to it, and checks
on `/` from four regions every minute add up. Point the monitor at a health route with
`appsignal-gateway-operator/path: /api/health`, and exclude that action in the app's
`ignoreActions`.

### Alerting is not managed

`NOTIFIER_IDS` is applied when a monitor is created and never on an update. On an
update the operator sends back the notifiers and headers the monitor already has.

A new monitor should not start out silent. But once it exists, who gets alerted belongs
to the people on call, and a reconcile that put their change back every fifteen minutes
would be worse than never setting it.

## What is fixed

AppSignal decides a few things about every uptime monitor, so there is nothing for the
operator to configure:

- **Check frequency and timeout** are fixed: every minute, 30 seconds.
- **Expected status codes** are fixed to 2xx.
- **Maintenance windows** do not exist. The nearest thing is `WARMUP_MINUTES`: an alert
  opens only after the check has failed that long, which absorbs a node drain without
  a timetable. Unlike a window, checks keep running, so your uptime figures stay
  complete.

## Reconciliation

Watch events handle the normal case within seconds. On top of that a full resync runs
every `RESYNC_SECONDS`: it lists every route, computes the complete desired set, and
makes the app's owned monitors match. A missed watch event, a restart during a deletion,
or a monitor someone removed by hand all converge on the next pass.

Deletion is handled there rather than by a finalizer, deliberately. A finalizer that
cannot reach AppSignal would block the deletion of someone's route.

## Uninstalling

Removing the operator leaves every monitor where it is, on purpose. A monitor left
behind complains loudly and gets cleaned up. Monitoring that disappears quietly is
discovered during the next outage nobody was paged for. When you do mean it, delete the
monitors (or the app) in AppSignal yourself.

## Versions

Images are published to `ghcr.io/sevenlabnl/appsignal-gateway-operator`. Releases are
git tags, and each one publishes `X.Y.Z`, `X.Y`, `X` and `latest`. Pre-releases never
become `latest`. Builds from `main` are published as `:main` and as the commit sha.

Below 1.0 the annotation names and configuration keys may still change.

## Developing

```bash
pip install -e ".[dev]"
pytest -q
```

The rules and the plan are pure functions, tested without a cluster or a network. The
AppSignal client is tested against a fake GraphQL transport.

## Licence

Apache 2.0.
