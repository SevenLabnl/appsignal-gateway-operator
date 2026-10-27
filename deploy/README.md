# Deploying

Three things are not in `operator.yaml` because they differ per cluster and one of them
is a secret.

**The cluster name** marks which monitors this operator owns. Two clusters sharing a
name would delete each other's monitors on the first resync, so the operator refuses
to start without it.

**The app id** is the AppSignal app the monitors are created in. Uptime monitors
belong to an app; give each cluster its own, so the monitor list for an app is the
list for one cluster. The id is on the app's settings screen.

```bash
kubectl create configmap appsignal-gateway-operator-cluster -n appsignal-gateway-operator \
  --from-literal=CLUSTER_NAME=production-cluster \
  --from-literal=APPSIGNAL_APP_ID=<app id>
```

**The token** is an AppSignal personal API token, from *Personal settings → API
tokens*.

```bash
kubectl create secret generic appsignal-gateway-operator-token -n appsignal-gateway-operator \
  --from-literal=token=<token>
```

If you run External Secrets, project it from your secret store instead of creating it
by hand. The key must be `token`.

Then:

```bash
kubectl apply -f operator.yaml
```

## Start in dry run

`DRY_RUN` is `"true"` in the shipped ConfigMap on purpose. The operator will log the
plan it would carry out and touch nothing:

```
[dry-run] create https://shop.example.com/ (shop.example.com)
[dry-run] delete https://old.example.com/
```

Read that list. When it says what you expect:

```bash
kubectl set env deploy/appsignal-gateway-operator -n appsignal-gateway-operator DRY_RUN=false
```

A deletion in that list is worth a second look: it means a monitor carries this
cluster's marker but no HTTPRoute accounts for it. Usually that is a hostname that
went away. Sometimes it is a route that has not been applied yet.

## What it needs

Cluster-wide `get`, `list` and `watch` on `httproutes`. No write access anywhere. The
comment in `operator.yaml` explains why that is possible.
