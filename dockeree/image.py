from collections import deque
from pathlib import Path
import shutil
import subprocess as sp
import tempfile
import time
import docker
from loguru import logger
import pygit2
import pytest
from .utils import reg_tag, get_docker_builder, DockerActionResult
from .node import Node


def _ignore_socket(dir_, files):
    dir_ = Path(dir_)
    return [file for file in files if (dir_ / file).is_socket()]


class DockerImage:
    """Class representing a Docker Image."""

    DOCKERFILE = "Dockerfile"

    def __init__(
        self,
        git_url: str,
        branch: str = "dev",
        branch_fallback: str = "dev",
        repo_path: dict[str, Path] | None = None,
        root_image_name: str = "",
    ):
        """Initialize a DockerImage object.

        :param git_url: URL of the remote Git repository.
        :param branch: The branch of the GitHub repository to use.
        """
        self._git_url = git_url[:-4] if git_url.endswith(".git") else git_url
        self._branch = branch
        self._branch_fallback = branch_fallback
        self._repo_path: dict[str, Path] = {} if repo_path is None else repo_path
        self._path = None
        self._name = ""
        self._base_image = ""
        self._root_image_name = root_image_name.split(":")[0] + ":"
        self._git_url_base = ""

    def is_root(self) -> bool:
        """Check whether this DockerImage is a root DockerImage."""
        return (
            self._base_image.startswith(self._root_image_name) or not self._git_url_base
        )

    def clone_repo(self) -> None:
        """Clone the Git repository to a local directory."""
        if self._path:
            return
        if self._git_url in self._repo_path:
            self._path = self._repo_path[self._git_url]
            repo = pygit2.Repository(self._path)
            logger.info(
                "{} has already been cloned into {} previously.",
                self._git_url,
                self._path,
            )
        else:
            self._path = Path(tempfile.mkdtemp())
            logger.info("Cloning {} into {}", self._git_url, self._path)
            repo = pygit2.clone_repository(self._git_url, self._path)
            self._repo_path[self._git_url] = self._path
        self._checkout_branch(repo)
        self._parse_dockerfile()

    def _checkout_branch(self, repo) -> None:
        """Checkout the branch self._branch from repo if the branch exists.
        Otherwise, create a new branch named self._branch in repo and checkout it.
        """
        repo.reset(repo.head.peel().id, pygit2.GIT_RESET_HARD)  # pylint: disable=E1101
        if repo.branches.get(self._branch) is None:
            for ref in [
                f"refs/remotes/origin/{self._branch}",
                f"refs/heads/{self._branch_fallback}",
                f"refs/remotes/origin/{self._branch_fallback}",
            ]:
                ref = repo.references.get(ref)
                if ref:
                    repo.create_branch(self._branch, ref.peel())
                    break
        repo.checkout(f"refs/heads/{self._branch}")

    def _parse_dockerfile(self):
        if self._path is None:
            raise RuntimeError(
                "DockerImage._parse_dockerfile can only be called after DockerImage._path is set."
            )
        dockerfile = self._path / DockerImage.DOCKERFILE
        with dockerfile.open() as fin:
            for line in fin:
                if line.startswith("# NAME:"):
                    self._name = line[7:].strip()
                    logger.info("This image name: {}", self._name)
                elif line.startswith("FROM "):
                    self._base_image = line[5:].strip()
                    if ":" not in self._base_image:
                        self._base_image += ":latest"
                    logger.info("Base image name: {}", self._base_image)
                elif line.startswith("# GIT:"):
                    self._git_url_base = line[6:].strip()
                    logger.info("Base image URL: {}", self._git_url_base)
        if not self._name:
            raise LookupError("The name tag '# NAME:' is not found in the Dockerfile!")
        if not self._base_image:
            raise LookupError("The FROM line is not found in the Dockerfile!")

    def get_deps(self, repo_branch) -> "deque[DockerImage]":
        """Get all dependencies of this DockerImage in order.

        :param repo_branch: A set-like collection containing tuples of (git_url, branch).
        :return: A deque containing dependency images.
        """
        self.clone_repo()
        deps = deque([self])
        obj = self
        while (
            obj._git_url_base,  # pylint: disable=W0212
            obj._branch,  # pylint: disable=W0212
        ) not in repo_branch:
            if obj.is_root():
                break
            obj = obj.base_image()
            deps.appendleft(obj)
        return deps

    def base_image(self) -> "DockerImage":
        """Get the base DockerImage of this DockerImage."""
        image = DockerImage(
            git_url=self._git_url_base,
            branch=self._branch,
            branch_fallback=self._branch_fallback,
            repo_path=self._repo_path,
            root_image_name=self._root_image_name,
        )
        image.clone_repo()
        return image

    def _copy_ssh(self, copy_ssh_to: str):
        if not copy_ssh_to:
            return
        if self._path is None:
            raise RuntimeError(
                "DockerImage._parse_dockerfile can only be called after DockerImage._path is set."
            )
        ssh_src = Path.home() / ".ssh"
        if not ssh_src.is_dir():
            logger.warning("~/.ssh does NOT exists!")
            return
        ssh_dst = self._path / copy_ssh_to
        try:
            shutil.rmtree(ssh_dst)
        except FileNotFoundError:
            pass
        shutil.copytree(ssh_src, ssh_dst, ignore=_ignore_socket)
        logger.info("~/.ssh has been copied to {}", ssh_dst)

    def build(
        self,
        tags: None | str | list[str] = None,
        copy_ssh_to: str = "",
        builder: str = get_docker_builder(),
    ) -> DockerActionResult:
        """Build the Docker image.

        :param tags: The tags of the Docker image to build.
            If None (default), then it is determined by the branch name.
            When the branch is master the "latest" tag is used,
            otherwise the next tag is used.
            If an empty string is specifed for tags,
            it is also treated as the latest tag.
        :param copy_ssh_to: If True, SSH keys are copied into a directory named ssh
            under the current local Git repository.
        :param builder: The tool to use to build Docker images.
        :return: A tuple of the format (image_name_built, tag_built, time_taken, "build").
        """
        time_begin = time.perf_counter_ns()
        self.clone_repo()
        self._copy_ssh(copy_ssh_to)
        tags = reg_tag(tags, self._branch)
        tag0 = tags[0]
        image_tag = f"{self._name}:{tag0}"
        logger.info("Building the Docker image {} ...", image_tag)
        self._update_base_tag(tag0)
        images = docker.from_env().images
        try:
            images.remove(image_tag, force=True)
        except Exception:
            pass
        try:
            if builder == "docker":
                for msg in docker.APIClient(
                    base_url="unix://var/run/docker.sock"
                ).build(
                    path=str(self._path),
                    tag=image_tag,
                    rm=True,
                    pull=self.is_root(),
                    cache_from=None,
                    decode=True,
                ):
                    if "stream" in msg:
                        print(f"[{image_tag}] {msg['stream']}", end="")
                # add additional tags for the image
                image = images.get(image_tag)
                for tag in tags[1:]:
                    image.tag(self._name, tag, force=True)
            elif builder == "/kaniko/executor":
                dests = " ".join(f"-d {self._name}:{tag}" for tag in tags)
                cmd = f"/kaniko/executor --cleanup -c {self._path} {dests}"
                sp.run(cmd, shell=True, check=True)
            elif builder == "":
                raise ValueError("Please provide a valid Docker builder!")
            else:
                raise NotImplementedError(
                    f"The docker builder {builder} is not supported yet!"
                )
        except docker.errors.BuildError as err:  # ty: ignore [possibly-missing-attribute]
            return DockerActionResult(
                succeed=False,
                err_msg="\n".join(
                    line.get("stream", line.get("error")) for line in err.build_log
                ),
                image=self._name,
                tag="",
                action="build",
                seconds=(time.perf_counter_ns() - time_begin) / 1e9,
            )
        except docker.errors.ImageNotFound:  # ty: ignore [possibly-missing-attribute]
            return DockerActionResult(
                succeed=False,
                err_msg="",
                image=self._name,
                tag="",
                action="build",
                seconds=(time.perf_counter_ns() - time_begin) / 1e9,
            )
        finally:
            self._remove_ssh(copy_ssh_to)
        if self._test_built_image():
            return DockerActionResult(
                succeed=True,
                err_msg="",
                image=self._name,
                tag=tag0,
                action="build",
                seconds=(time.perf_counter_ns() - time_begin) / 1e9,
            )
        return DockerActionResult(
            succeed=False,
            err_msg="Built image failed to pass tests.",
            image=self._name,
            tag=tag0,
            action="build",
            seconds=(time.perf_counter_ns() - time_begin) / 1e9,
        )

    def _test_built_image(self) -> bool:
        code = pytest.main([str(self._path)])
        return code in (pytest.ExitCode.OK, pytest.ExitCode.NO_TESTS_COLLECTED, 0)

    def _remove_ssh(self, copy_ssh_to: str):
        if not copy_ssh_to:
            return
        if self._path is None:
            raise RuntimeError(
                "DockerImage._parse_dockerfile can only be called after DockerImage._path is set."
            )
        try:
            shutil.rmtree(self._path / copy_ssh_to)
        except FileNotFoundError:
            pass

    def _update_base_tag(self, tag_build: str) -> None:
        if not self._git_url_base:  # self is a root image
            return
        if self._path is None:
            raise RuntimeError(
                "DockerImage._parse_dockerfile can only be called after DockerImage._path is set."
            )
        dockerfile = self._path / DockerImage.DOCKERFILE
        with dockerfile.open() as fin:
            lines = fin.readlines()
        for idx, line in enumerate(lines):
            if line.startswith("FROM "):
                lines[idx] = line[: line.rfind(":")] + f":{tag_build}\n"
                break
        with dockerfile.open("w") as fout:
            fout.writelines(lines)

    def node(self):
        """Convert this DockerImage to a Node."""
        return Node(
            git_url=self._git_url,
            branch=self._branch,
        )

    def base_node(self):
        """Convert the base image of this DockerImage to a Node."""
        return self.base_image().node()

    def docker_servers(self) -> set[str]:
        """Get 3rd-party Docker image hosts associated with this DockerImage and its base DockerImage.

        :return: A set of 3rdd-party Docker image hosts.
        """
        servers = set()
        if self._base_image.count("/") > 1:
            servers.add(self._base_image.split("/", maxsplit=1)[0])
        if self._name.count("/") > 1:
            servers.add(self._name.split("/", maxsplit=1)[0])
        return servers
