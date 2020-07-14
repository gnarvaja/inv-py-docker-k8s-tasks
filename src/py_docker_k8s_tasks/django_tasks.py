from invoke import task
from .docker_tasks import docker_exec


@task
def django(c, port=8000):
    docker_exec(c, "./manage.py runserver 0:{}".format(port))


@task
def gunicorn(c, port=8000):
    gunicorn_config = c.config.get("gunicorn_config", "/usr/local/app/gunicorn.py")
    if gunicorn_config:
        config = f"--config {gunicorn_config}"
    else:
        config = ""
    wsgi_app = c.config.get("wsgi_app", None)
    if wsgi_app is None:
        app = c.config.app
        wsgi_app = f"{app}.wsgi:application"
    docker_exec(c, f"/usr/local/bin/gunicorn {config} -b :{port} {wsgi_app}")


@task
def celery(c):
    app = c.config.app
    celery_args = c.config.get("celery_args", "-P solo -c1")
    docker_exec(c, f"celery worker --app={app} {celery_args}")


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
def manage(c, command):
    docker_exec(c, "./manage.py {}".format(command))


@task
def makemessages(c, language=None):
    if language:
        languages = [language]
    else:
        languages = c.config.translations.languages
    extra_params = c.config.translations.get("extra_params", [])
    for lang in languages:
        docker_exec(c, "./manage.py makemessages -l {} {}".format(lang, " ".join(extra_params)))


@task
def compilemessages(c, language=None):
    docker_exec(c, "./manage.py compilemessages" + ("-l {}".format(language) if language else ""))


@task
def create_su(c, username="admin", email="testing@gogames.co"):
    docker_exec(c, "./manage.py createsuperuser --username {} --email {}".format(username, email))


@task
def coverage(c):
    docker_exec(c, "coverage run --source=. manage.py test")
    docker_exec(c, "coverage html")
