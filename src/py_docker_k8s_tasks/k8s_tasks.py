import os
import re
import sys
import tempfile
import base64
import yaml
from invoke import task, Failure


def kubectl(c, command, **kargs):
    env = getattr(c.config, "env", {})
    return c.run(f"kubectl {command}", env=env, **kargs)


def get_annotation(c, resource, name, annotation):
    command = f"get {resource} {name} -o=jsonpath='{{.metadata.annotations.{annotation}}}'"
    ret = kubectl(c, command, hide=True)
    return ret.stdout


def _normalize(dirname):
    if not dirname.endswith("/"):
        return dirname + "/"
    return dirname


def _applydelete(c, manifest, action="apply"):
    if manifest == "-":
        manifests = [f.strip() for f in sys.stdin.readlines()]
    else:
        manifests = [f.strip() for f in re.split(r",| |\n", manifest)
                     if f.strip()]
    ret = None
    for mfile in manifests:
        is_file = os.path.isfile(mfile)
        if not is_file and action == "apply":
            print(f"{mfile} does not exists!", file=sys.stderr)
        elif not is_file:  # action == delete
            # Assume it's pod to delete - To support multiple delete from stdin
            ret = kubectl(c, f"{action} pod {mfile}")
        else:
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
def krollout(c, name, action="restart", namespace=None):
    if namespace:
        namespace = f" -n {namespace}"
    else:
        namespace = ""
    if "/" not in name:
        name = "deployment/" + name
    kubectl(c, f"rollout {action} {name}{namespace}")


@task
def kc(c, command):
    return kubectl(c, command)


YTT_CREATE_CONFIGMAP = """#@ load("@ytt:data", "data")
apiVersion: v1
kind: ConfigMap
metadata:
  name: #@ data.values.name
  annotations: #@ data.values.annotations
data: #@ data.values.files
"""


YTT_CREATE_SECRET = """#@ load("@ytt:data", "data")
apiVersion: v1
kind: Secret
type: Opaque
metadata:
  name: #@ data.values.name
  annotations: #@ data.values.annotations
data: #@ data.values.files
"""


@task
def config_from_dir(c, name, directory=None, secret=False):
    config = "secret" if secret else "configmap"

    if "/" in name and os.path.isdir(name) and not directory:
        # name parameter is the directory, find the name of the configmap/secret
        name = _normalize(name)
        names = kubectl(c, f"get {config} -o=name", hide=True).stdout.splitlines()
        for n in names:
            n = n.split("/")[1]  # removes resource prefix
            if name == get_annotation(c, config, n, "config-from-dir"):
                directory, name = name, n
                break
        if not directory:
            raise Failure(f"No existing {config} found with config-from-dir={name}")

    if directory is None:
        directory = get_annotation(c, config, name, "config-from-dir")
        if not directory:
            raise Failure(f"Missing directory parameter and annotation not found")

    directory = _normalize(directory)

    template_file = tempfile.NamedTemporaryFile(suffix=".yaml", mode="wt")
    template_file.write(YTT_CREATE_SECRET if secret else YTT_CREATE_CONFIGMAP)
    template_file.flush()

    values = {"name": name, "annotations": {"config-from-dir": directory}, "files": {}}

    for filename in os.listdir(directory):
        file_str = open(os.path.join(directory, filename), "rb" if secret else "rt").read()
        if secret:
            values["files"][filename] = base64.b64encode(file_str)
        else:
            values["files"][filename] = file_str

    return run_ytt(c, template_file.name, values, apply=True)


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
def ktop(c, resource="nodes", cpu=None, memory=None):
    command =f"top {resource}"
    if cpu is None and memory is None:
        return kubectl(c, command)

    cpu = cpu and int(cpu)
    memory = memory and int(memory)

    out = kubectl(c, command, hide=True)
    lines = out.stdout.splitlines()

    for line in lines:
        line_cpu = re.findall(r"\s(\d+)m", line)
        line_memory = re.findall(r"\s(\d+)Mi", line)
        if cpu and line_cpu:
            line_cpu = int(line_cpu[0])
            if cpu > 0 and line_cpu < cpu:
                # Skip lines with LESS than CPU parameter
                continue
            if cpu < 0 and line_cpu > -cpu:
                # Skip lines with MORE than CPU parameter
                continue
        if memory and line_memory:
            line_memory = int(line_memory[0])
            if memory > 0 and line_memory < memory:
                # Skip lines with LESS than memory parameter
                continue
            if memory < 0 and line_memory > -memory:
                # Skip lines with MORE than memory parameter
                continue
        print(line.rstrip("\n"))


@task
def kget(c, resource="pods", grep=None, status=None, keep_header=True, namespace=None,
         name=None, app=None, llist=False):
    if grep:
        hide = "out"
    else:
        hide = None

    if llist:
        keep_header = False

    if namespace is None:
        namespace = ""
    elif namespace == "all":
        namespace = "--all-namespaces"
    else:
        namespace = f"-n={namespace}"

    label_filter = ""
    if app:
        label_filter += f" -l app={app}"
    if name:
        label_filter += f" -l name={name}"

    out = kubectl(c, f"get {resource} {namespace}{label_filter}", hide=hide)
    if hide is None:
        return

    lines = out.stdout.splitlines()

    if grep:
        lines = [l for i, l in enumerate(lines) if grep in l or keep_header and i == 0]

    if llist:
        for l in lines:
            # Only first column, the name
            print(l.split()[0])
    else:
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

    if apply and not output_file:
        env = getattr(c.config, "env", {})
        return c.run(f"ytt {f_param} | kubectl apply -f -", env=env, **kargs)

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
