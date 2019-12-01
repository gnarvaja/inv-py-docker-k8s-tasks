import os
import requests
from invoke import task


def _get_aws_token(c):
    token = os.getenv("AWS_TOKEN")
    if not token:
        token = c.run("aws ecr get-authorization-token --output text "
                      "--query 'authorizationData[].authorizationToken'", hide=True).stdout.strip()
    return token


def _get_last_version(c, registry, image):
    token = _get_aws_token(c)
    url = 'https://{}/v2/{}/tags/list'.format(registry, image)
    r = requests.get(url, headers={'Authorization': 'Basic {}'.format(token)})
    r.raise_for_status()
    return sorted(r.json()['tags'])[-1]


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
    print(_get_next_version(registry, image))


def docker_exec(c, command, container=None, pty=True):
    container = container or c.config.container
    c.run("docker exec {} {} {}".format("-it" if pty else "", container, command), pty=True)


@task
def start_dev(c):
    c.run("docker-compose -f docker-compose.yml -f docker-compose.override.dev.yml up --build -d")


@task
def start(c):
    c.run("docker-compose -f docker-compose.yml --build -d")


@task
def stop(c):
    c.run("docker-compose down")


@task
def shell(c):
    shell = c.config.get("container_shell", "sh")
    docker_exec(c, shell)


@task
def build(c, registry=None, image=None, version=None):
    registry, image = _default_registry_image(c, registry, image)
    version = version or _get_next_version(c, registry, image)
    c.run("docker build -t {}/{}:{} .".format(registry, image, version))


@task
def push_image(c, registry=None, image=None, version=None):
    registry, image = _default_registry_image(c, registry, image)
    version = version or _get_next_version(c, registry, image)
    docker_login_cmd = c.run("aws ecr get-login --no-include-email", hide=True).stdout
    c.run(docker_login_cmd)
    c.run("docker push {}/{}:{}".format(registry, image, version))
