import os
import re
import sys
from invoke import task, Failure


def _kubectl(c, command, **kargs):
    env = getattr(c.config, "env", {})
    return c.run(f"kubectl {command}", env=env, **kargs)


@task
def apply(c, manifest):
    if manifest == "-":
        manifests = [f.strip() for f in sys.stdin.readlines()]
    else:
        manifests = [f.strip() for f in re.split(r",| |\n", manifest)
                     if f.strip()]
    ret = None
    for mfile in manifests:
        if not os.path.isfile(mfile):
            print(f"{mfile} does not exists!", file=sys.stderr)
            continue
        ret = _kubectl(c, f"apply -f {mfile}")
    return ret


@task
def kc(c, command):
    return _kubectl(c, command)


@task
def config_from_dir(c, name, directory, secret=False):
    config = "secret" if secret else "configmap"
    command = f"create"
    if secret:
        command += f" secret generic {name}"
    else:
        command += " configmap {name}"
    for filename in os.listdir(directory):
        command += " --from-file " + os.path.join(directory, filename)
    if _kubectl(c, f"get {config} {name}", warn=True, hide="both"):
        # Exists
        command += " -o yaml --dry-run=client | kubectl replace -f -"
    return _kubectl(c, command)


@task
def logs(c, podname, zfuzzy=False, follow=False, tail=None):
    if zfuzzy:
        pods = _kubectl(c, "get pods", hide="out")
        podnames = [re.split(" +", l)[0] for l in pods.stdout.splitlines()[1:]]
        if podname not in podnames:
            aux = [p for p in podnames if podname in p]
            if aux and len(aux) == 1:
                podname = aux[0]
            else:
                podnames = ",".join(podnames)
                raise Failure(f"{podname} not found in pods: {podnames}!")
    follow = "--follow" if follow else ""
    tail = f"--tail {tail}" if tail else ""
    return _kubectl(c, f"logs {podname} {follow} {tail}")


@task
def kget(c, resource="pods", grep=None, status=None, keep_header=True):
    if grep:
        hide = "out"
    else:
        hide = None

    out = _kubectl(c, f"get {resource}", hide=hide)
    if hide is None:
        return

    lines = out.stdout.splitlines()

    if grep:
        lines = [l for i, l in enumerate(lines) if grep in l or keep_header and i == 0]

    for l in lines:
        print(l.rstrip("\n"))
