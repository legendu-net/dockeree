from collections import namedtuple
from pathlib import Path
import shutil


def branch_to_tag(branch: str) -> str:
    """Convert a branch to its corresponding Docker image tag.

    :param branch: A branch name.
    :return: The Docker image tag corresponding to the branch.
    """
    if branch in ("master", "main"):
        return "latest"
    if branch == "dev":
        return "next"
    return branch


def reg_tag(tag: None | str | list[str], branch: str):
    if tag is None:
        tag = branch_to_tag(branch)
    elif tag == "":
        tag = "latest"
    if isinstance(tag, str):
        tag = [tag]
    return tag


def get_docker_builder() -> str:
    docker = "docker"
    if shutil.which(docker):
        return docker
    kaniko = "/kaniko/executor"
    if Path(kaniko).is_file():
        return kaniko
    return ""


DockerActionResult = namedtuple(
    "DockerActionResult", ["succeed", "err_msg", "image", "tag", "action", "seconds"]
)
