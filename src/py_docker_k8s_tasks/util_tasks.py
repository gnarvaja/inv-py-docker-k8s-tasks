import time
import re
from invoke import task
from invoke.tasks import Task

REGEX_TYPE = type(re.compile('hello, world'))


@task
def sleep(c, sleep_time=5):
    time.sleep(sleep_time)


mount_ramdisk = """
if [ ! -d {path} ]; then
    {sudo} mkdir {path};
fi
if mount | grep -q {path}; then
    echo "ramdisk already mounted!"
else
    {sudo} mount -t tmpfs -o size={size} tmpfs {path}
    echo Done! umount {path} to release the memory
fi
"""


@task
def ramdisk(c, path="/mnt/memdisk", size="300m", sudo=True):
    sudo = "sudo" if sudo else ""
    c.run(mount_ramdisk.format(**locals()))


def _filter_task(task, filter):
    if filter is None:
        return True
    elif isinstance(filter, str):
        return task.name == filter
    elif isinstance(filter, REGEX_TYPE):
        return filter.match(task.name) is not None
    elif isinstance(filter, list):
        # List of task names
        for f in filter:
            if _filter_task(task):
                return True
        return False
    raise NotImplementedError("Unrecognized filter: {}".format(filter))


def add_tasks(namespace, module, filter=None):
    for var_name in dir(module):
        task_ = getattr(module, var_name)
        if not isinstance(task_, Task):
            continue

        if not _filter_task(task_, filter):
            continue

        namespace.add_task(task_)
