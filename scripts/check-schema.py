"""Read-only check that the GraphQL fields the provider uses exist in AppSignal's live schema.

Run with APPSIGNAL_TOKEN set; pass an app id to also run the provider's own list query.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from gateway_uptime.providers.appsignal import LIST  # noqa: E402

INTROSPECT = """{
  input: __type(name: "UptimeMonitorInput") { inputFields { name type { ...T } } }
  kv: __type(name: "KeyStringValueInput") { inputFields { name type { ...T } } }
  schema: __schema { mutationType { fields { name args { name type { ...T } } } } }
}
fragment T on __Type { kind name ofType { kind name ofType { kind name } } }"""


def call(query, variables=None):
    url = "https://appsignal.com/graphql?" + urllib.parse.urlencode(
        {"token": os.environ.get("APPSIGNAL_TOKEN")
                  or os.environ["APPSIGNAL_API_TOKEN"]})
    req = urllib.request.Request(url, method="POST", headers={
        "Content-Type": "application/json"},
        data=json.dumps({"query": query, "variables": variables or {}}).encode())
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def typename(t):
    if t is None:
        return "?"
    if t["kind"] == "NON_NULL":
        return typename(t["ofType"]) + "!"
    if t["kind"] == "LIST":
        return "[" + typename(t["ofType"]) + "]"
    return t["name"]


res = call(INTROSPECT)
if res.get("errors"):
    print("errors:", res["errors"])
d = res.get("data") or {}
for label, key in (("UptimeMonitorInput", "input"), ("KeyStringValueInput", "kv")):
    print(label)
    for f in (d.get(key) or {}).get("inputFields") or []:
        print("  %-22s %s" % (f["name"], typename(f["type"])))
print("mutations")
for m in d["schema"]["mutationType"]["fields"]:
    if "UptimeMonitor" in m["name"]:
        print("  %s(%s)" % (m["name"], ", ".join(
            "%s: %s" % (a["name"], typename(a["type"])) for a in m["args"])))

if len(sys.argv) > 1:
    res = call(LIST, {"appId": sys.argv[1]})
    if res.get("errors"):
        print("list errors:", res["errors"])
    else:
        mons = ((res.get("data") or {}).get("app") or {}).get("uptimeMonitors")
        print("list query ok, %s monitor(s)" % (len(mons) if mons is not None else "no app"))
