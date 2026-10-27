# Security

## Reporting

Please report vulnerabilities privately through GitHub's
[security advisories](https://github.com/SevenLabnl/appsignal-gateway-operator/security/advisories/new)
rather than as a public issue.

## What this operator can reach

Worth knowing before you install it.

**In the cluster:** cluster-wide read, and only read. `get`, `list` and `watch` on
`httproutes`, plus `list` and `watch` on `customresourcedefinitions` and `namespaces`,
which kopf needs to resolve the resource it watches. There is no write verb in its
`ClusterRole` — it never annotates, patches or deletes anything in Kubernetes. That is
possible because ownership of a monitor is recorded in AppSignal rather than on your
resources.

**Outside the cluster:** it holds an AppSignal personal API token and uses it to
create, update and delete uptime monitors in the one app named by `APPSIGNAL_APP_ID`.
Within that app it only touches monitors whose description starts with its marker for
`CLUSTER_NAME`; anything else is never read as ours and never removed.

The token is the sensitive part. A personal token acts with all the rights of the user
who made it, across every app and organisation that user can see, and AppSignal offers
no narrower scope for this API. Create it on an account that has access to the apps
this operator writes to and little else, and project it from a secret store if you run
one. AppSignal only accepts the token as a query parameter; the operator keeps it out
of its own log lines and error messages, but anything that logs full outbound URLs
will see it.

Hostnames from your routes are sent to AppSignal, which is the point, but worth stating
plainly if any of them are not public.

The container runs as uid 65532 with a read-only root filesystem, no capabilities and
no privilege escalation.
