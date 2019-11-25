from invoke import task
from .docker_tasks import docker_exec


@task
def django(c, port=8000):
    docker_exec(c, "./manage.py runserver 0:{}".format(port))


@task
def djshell(c):
    docker_exec(c, "./manage.py shell")


@task
def test(c, test=""):
    docker_exec(c, "./manage.py test {}".format(test))


@task
def migrate(c):
    docker_exec(c, "./manage.py migrate")


@task
def create_su(c, username="admin", email="testing@gogames.co"):
    docker_exec(c, "./manage.py createsuperuser --username {} --email {}".format(username, email))


@task
def coverage(c):
    docker_exec(c, "coverage run --source=. manage.py test")
    docker_exec(c, "coverage html")
