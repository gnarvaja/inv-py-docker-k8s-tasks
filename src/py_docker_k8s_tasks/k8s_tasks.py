import os
import re
import sys
import tempfile
import yaml
from invoke import task, Failure


def kubectl(c, command, **kargs):
    env = getattr(c.config, "env", {})
    return c.run(f"kubectl {command}", env=env, **kargs)


def _applydelete(c, manifest, action="apply"):
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
        ret = kubectl(c, f"{action} -f {mfile}")
    return ret


@task
def apply(c, manifest):
    return _applydelete(c, manifest, "apply")


@task
def kdelete(c, manifest, resource=None):
    if manifest == "-" or os.path.isfile(manifest):
        return _applydelete(c, manifest, "delete")
    else:
        resource = resource or "pod"
        kubectl(c, f"delete {resource} {manifest}")


@task
def kdescribe(c, resource, name, namespace=None):
    if namespace:
        namespace = f" -n {namespace}"
    else:
        namespace = ""
    kubectl(c, f"describe {resource} {name}{namespace}")


@task
def kc(c, command):
    return kubectl(c, command)


@task
def config_from_dir(c, name, directory, secret=False, create=False):
    config = "secret" if secret else "configmap"
    command = f"create"
    if secret:
        command += f" secret generic {name}"
    else:
        command += f" configmap {name}"
    for filename in os.listdir(directory):
        command += " --from-file " + os.path.join(directory, filename)
    if kubectl(c, f"get {config} {name}", warn=True, hide="both"):
        # Exists
        command += " -o yaml --dry-run=client | kubectl replace -f -"
    elif not create:
        raise Failure(f"{config} does not exist, add --create if you want to create")
    return kubectl(c, command)


def _fuzzy_find_pod(c, podname):
    pods = kubectl(c, "get pods", hide="out")
    podnames = [re.split(" +", l)[0] for l in pods.stdout.splitlines()[1:]]
    if podname not in podnames:
        aux = [p for p in podnames if podname in p]
        if aux and len(aux) == 1:
            podname = aux[0]
        elif len(aux) > 1:
            podnames = ", ".join(aux)
            raise Failure(f"More than one pod matching {podname}: {podnames}!")
        else:
            podnames = ", ".join(podnames)
            raise Failure(f"{podname} not found in pods: {podnames}!")
    return podname


@task
def logs(c, podname, zfuzzy=False, app=False, name=False, follow=False, tail=None,
         container=None, max_log_requests=None):
    if zfuzzy:
        podname = _fuzzy_find_pod(c, podname)
    elif app:
        podname = f"-l app={podname}"
    elif name:
        podname = f"-l name={podname}"

    if max_log_requests:
        max_log_requests = f"--max-log-requests {max_log_requests}"
    else:
        max_log_requests = ""

    follow = "--follow" if follow else ""
    tail = f"--tail {tail}" if tail else ""
    container = f"-c {container}" if container else ""
    return kubectl(c, f"logs {podname} {follow} {tail} {container} {max_log_requests}")


@task
def kshell(c, podname, zfuzzy=False, shell="sh"):
    if zfuzzy:
        podname = _fuzzy_find_pod(c, podname)
    return kubectl(c, f"exec -it {podname} -- {shell}", pty=True)


@task
def ktop(c, resource="nodes"):
    return kubectl(c, f"top {resource}")


@task
def kget(c, resource="pods", grep=None, status=None, keep_header=True, namespace=None):
    if grep:
        hide = "out"
    else:
        hide = None

    if namespace is None:
        namespace = ""
    elif namespace == "all":
        namespace = "--all-namespaces"
    else:
        namespace = f"-n={namespace}"

    out = kubectl(c, f"get {resource} {namespace}", hide=hide)
    if hide is None:
        return

    lines = out.stdout.splitlines()

    if grep:
        lines = [l for i, l in enumerate(lines) if grep in l or keep_header and i == 0]

    for l in lines:
        print(l.rstrip("\n"))


def run_ytt(c, template, values=None, output_file=None, apply=False, **kargs):
    f_param = [f"-f {template}"]

    if values is not None:
        values_file = tempfile.NamedTemporaryFile(mode="wt", suffix=".yml")
        values_file.write("#@data/values\n---\n")
        yaml.safe_dump(values, values_file)
        f_param.insert(1, f"-f {values_file.name}")
        values_file.flush()

    if output_file:
        output = f"> {output_file}"
    else:
        output = ""

    f_param = " ".join(f_param)

    ret = c.run(f"ytt {f_param} {output}", **kargs)

    if apply:
        ret = kubectl(c, f"apply -f {output_file}", **kargs)
    return ret


@task(iterable=["value_list"])
def ytt(c, template, values=[], output_file=None, apply=False):
    """Processes a manifest with string.Template using os.environ, config.env, and
       config.template_vars[output_file]
    """
    values_dict = dict(v.split("=") for v in values)
    return run_ytt(c, template, values=values_dict, apply=apply)


@task
def generate_templates(c, template_file=None, output_file=None, apply=False):
    templates = c.config.templates

    for template_filename, params in templates.items():
        if template_file and template_filename != template_file:
            continue  # Only process specific template
        default_values = params.get("values", {})
        for out_file_config in params.get("files", []):
            out_file = out_file_config["name"]
            if output_file and output_file != out_file:
                continue  # Only generate specific output
            values = dict(default_values)
            values.update(out_file_config.get("values", {}))
            run_ytt(c, template_filename, values=values,
                    output_file=out_file, apply=apply)
