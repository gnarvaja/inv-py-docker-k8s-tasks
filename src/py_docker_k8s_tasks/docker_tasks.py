import os
import re
import requests
from invoke import task


def _get_aws_token(c):
    token = os.getenv("AWS_TOKEN")
    if not token:
        token = c.run("aws ecr get-authorization-token --output text "
                      "--query 'authorizationData[].authorizationToken'", hide=True).stdout.strip()
    return token


def _get_gcloud_token(c):
    token = os.getenv("GCLOUD_TOKEN")
    if not token:
        token = c.run("gcloud auth print-access-token", hide=True).stdout.strip()
    return token


def _version_to_int(version):
    """Converts a version number into an integer number, so it can be sorted

    >>> _version_to_int("0.1.1")
    1001
    >>> _version_to_int("1.2.3")
    1002003
    >>> _version_to_int("2001")
    2001
    >>> _version_to_int("latest")
    0
    """
    if version == "latest":
        return 0
    components = version.split(".")
    ret = 0
    for i, comp in enumerate(components):
        ret += int(comp) * (1000 ** (len(components) - (i + 1)))
    return ret


def _registry_type(registry):
    if "amazonaws" in registry:
        return "aws"
    elif "gcr.io" in registry:
        return "googlecloud"
    elif "pkg.dev" in registry:
        return "googlecloud"
    elif "icr.io" in registry:
        return "ibmcloud"
    elif registry == "":
        return "dockerhub"
    else:
        return "unknown"


def _join(registry, image):
    if not registry:
        return image
    return "{}/{}".format(registry, image)


def _auth_headers(c, registry):
    if _registry_type(registry) == "aws":
        token = _get_aws_token(c)
        return dict(headers={'Authorization': 'Basic {}'.format(token)})
    elif _registry_type(registry) == "googlecloud":
        token = _get_gcloud_token(c)
        return dict(auth=("oauth2accesstoken", token))
    else:
        return {}


def _get_last_version_from_local_docker(c, registry, image):
    registry_image = _join(registry, image)
    output = c.run(f"docker image ls {registry_image}", hide="out")
    # Black magic explanation: skips first line (header), 2nd field is version
    tags = [re.split(" +", lin)[1] for lin in output.stdout.splitlines()[1:]]
    return sorted(tags, key=_version_to_int)[-1]


def _get_last_version(c, registry, image):
    if _registry_type(registry) in ("ibmcloud", "dockerhub"):
        # fallback, don't know how to get tabs from ibmcloud registry
        return _get_last_version_from_local_docker(c, registry, image)

    url = 'https://{}/v2/{}/tags/list'.format(registry, image)
    r = requests.get(url, **_auth_headers(c, registry))
    r.raise_for_status()
    tags = r.json()['tags']
    if len(tags) == 100:
        raise RuntimeError(
            "Error, the response has 100 tags, we hit the limit and paging not supported, "
            "you should remove some tags in ECR console"
        )
    return sorted(tags, key=_version_to_int)[-1]


def _get_next_version(c, registry, image):
    registry, image = _default_registry_image(c, registry, image)
    version = _get_last_version(c, registry, image)
    parts = version.split('.')
    parts[-1] = str(int(parts[-1]) + 1)
    return '.'.join(parts)


def _default_registry_image(c, registry, image):
    if not registry:
        registry = c.config.registry

    if not image:
        image = c.config.image

    return registry, image


@task
def last_version(c, registry=None, image=None):
    registry, image = _default_registry_image(c, registry, image)
    print(_get_last_version(c, registry, image))


@task
def next_version(c, registry=None, image=None):
    registry, image = _default_registry_image(c, registry, image)
    print(_get_next_version(c, registry, image))


def docker_exec(c, command, container=None, pty=True, envs={}, workdir=None, user=None):
    container = container or c.config.container
    run_command = "docker exec "
    if pty:
        run_command += "-it "
    if user:
        run_command += f"-u {user} "
    if workdir:
        run_command += f"-w {workdir} "
    for env_var, env_value in envs.items():
        run_command += f"--env {env_var}={env_value} "

    for k, env_value in os.environ.items():
        if k.startswith("DOCKEREXEC_"):
            env_var = k.split('_', 1)[1]
            run_command += f"--env {env_var}={env_value} "

    c.run("{} {} {}".format(run_command, container, command), pty=pty)


@task
def docker_put(c, source, target, container=None):
    container = container or c.config.container
    c.run(f"docker cp {source} {container}:{target}")


@task
def docker_get(c, source, target, container=None):
    container = container or c.config.container
    c.run(f"docker cp {container}:{source} {target}")


def _compose_file():
    return os.getenv("COMPOSE_FILE", "docker-compose.yml")


@task
def start_dev(c, compose_files="docker-compose.override.dev.yml,docker-compose.override.local-dev.yml",
              detach=True):
    extra_param = ""
    for compose_file in compose_files.split(","):
        if os.path.exists(compose_file):
            extra_param += f"-f {compose_file} "

    detach = "-d" if detach else ""

    c.run(f"docker compose -f {_compose_file()} {extra_param} up --build {detach}")


@task
def start(c, detach=True):
    detach = "-d" if detach else ""
    c.run(f"docker compose -f {_compose_file()} up --build {detach}")


@task
def stop(c):
    c.run(f"docker compose down -f {_compose_file()}")


@task
def shell(c):
    shell = c.config.get("container_shell", "sh")
    docker_exec(c, shell)


@task
def pyshell(c):
    pyshell = c.config.get("container_pyshell", "ipython")
    docker_exec(c, pyshell)


@task
def build(c, registry=None, image=None, version=None):
    registry, image = _default_registry_image(c, registry, image)
    registry_image = _join(registry, image)
    version = version or _get_next_version(c, registry, image)
    c.run("docker build -t {}:{} .".format(registry_image, version))


@task
def push_image(c, registry=None, image=None, version=None):
    registry, image = _default_registry_image(c, registry, image)
    if not version:
        if _registry_type(registry) in ("ibmcloud", "dockerhub"):
            version = _get_last_version_from_local_docker(c, registry, image)
        else:
            version = _get_next_version(c, registry, image)
    if _registry_type(registry) == "aws":
        docker_login_cmd = c.run("aws ecr get-login --no-include-email", hide=True).stdout
        c.run(docker_login_cmd)
    registry_image = _join(registry, image)
    c.run("docker push {}:{}".format(registry_image, version))
