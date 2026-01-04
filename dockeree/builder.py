"""Docker related utils."""

from __future__ import annotations
from pathlib import Path
from typing import Callable
from collections import deque
import datetime
import subprocess as sp
import time
import yaml
from loguru import logger
import docker
import networkx as nx
import pygit2
from .utils import branch_to_tag, get_docker_builder, reg_tag, DockerActionResult
from .node import Node
from .image import DockerImage


def _push_image_timing(repo: str, tag: str) -> DockerActionResult:
    """Push a Docker image to Docker Hub and time the pushing.

    :param repo: The local repository of the Docker image.
    :param tag: The tag of the Docker image to push.
    :return: The time (in seconds) used to push the Docker image.
    """
    logger.info("Pushing Docker image {}:{} ...", repo, tag)
    time_begin = time.perf_counter_ns()
    try:
        retry(
            lambda: sp.run(f"docker push {repo}:{tag}", shell=True, check=True), times=3
        )
        return DockerActionResult(
            True, "", repo, tag, "push", (time.perf_counter_ns() - time_begin) / 1e9
        )
    except Exception as err:
        return DockerActionResult(
            False,
            str(err),
            repo,
            tag,
            "push",
            (time.perf_counter_ns() - time_begin) / 1e9,
        )


def retry(task: Callable, times: int = 3, wait_seconds: float = 60):
    """Retry a Docker API on failure (for a few times).
    :param task: The task to run.
    :param times: The total number of times to retry.
    :param wait_seconds: The number of seconds to wait before retrying.
    :return: The return result of the task.
    """
    for _ in range(1, times):
        try:
            return task()
        except Exception:
            time.sleep(wait_seconds)
    return task()


def tag_date(tag: str) -> str:
    """Suffix a tag with the current date as a 6-digit string.

    :param tag: A tag of Docker image.
    :return: A new tag.
    """
    mmddhh = datetime.datetime.now().strftime("%m%d%H")
    return mmddhh if tag in ("", "latest") else f"{tag}_{mmddhh}"


class DockerImageBuilderError(Exception):
    """Exception due to Docker image building."""


class DockerImageBuilder:
    """A class for build many Docker images at once."""

    def __init__(
        self,
        branch_urls: dict[str, dict[str, str]] | str | Path,
        branch_fallback: str = "dev",
        builder: str = get_docker_builder(),
    ):
        if isinstance(branch_urls, (str, Path)):
            with open(branch_urls, "r", encoding="utf-8") as fin:
                branch_urls = yaml.load(fin, Loader=yaml.FullLoader)
        self._branch_urls = branch_urls
        self._branch_fallback = branch_fallback
        self._graph = None
        self._repo_nodes: dict[str, list[Node]] = {}
        self._repo_path = {}
        self._roots = set()
        self.failures = []
        self._servers = set()
        self._builder = builder

    def _record_docker_servers(self, deps: deque[DockerImage]):
        for dep in deps:
            self._servers.update(dep.docker_servers())

    def _build_graph_branch(self, branch: str, urls: dict[str, str]):
        for url, root_image_name in urls.items():
            deps: deque[DockerImage] = DockerImage(
                git_url=url,
                branch=branch,
                branch_fallback=self._branch_fallback,
                repo_path=self._repo_path,
                root_image_name=root_image_name,
            ).get_deps(self._graph.nodes)
            self._record_docker_servers(deps)
            dep0 = deps.popleft()
            if dep0.is_root():
                node_prev = self._add_root_node(dep0.node())
            else:
                node_prev = self._find_identical_node(dep0.base_node())
                assert node_prev in self._graph.nodes
                self._add_edge(node_prev, dep0.node())
            for dep in deps:
                node_prev = self._add_edge(node_prev, dep.node())

    def _find_identical_node(self, node: Node) -> Node | None:
        """Find node in the graph which has identical branch as the specified dependency.
        Notice that a node in the graph is represented as (git_url, branch).

        :param node: A dependency of the type DockerImage.
        """
        logger.debug("Finding identical node of {} in the graph ...", node)
        nodes: list[Node] = self._repo_nodes.get(node.git_url, [])
        logger.debug("Nodes associated with the repo {}: {}", node.git_url, str(nodes))
        if not nodes:
            return None
        path = self._repo_path[node.git_url]
        for n in nodes:
            if self._compare_git_branches(path, n.branch, node.branch):
                return n
        return None

    @staticmethod
    def _compare_git_branches(path: str, b1: str, b2: str) -> bool:
        """Compare whether 2 branches of a repo are identical.

        :param path: The path to a local Git repository.
        :param b1: A branches.
        :param b2: Another branches.
        :return: True if there are no differences between the 2 branches and false otherwise.
        """
        logger.debug("Comparing branches {} and {} of the local repo {}", b1, b2, path)
        if b1 == b2:
            return True
        repo = pygit2.Repository(path)
        diff = repo.diff(f"refs/heads/{b1}", f"refs/heads/{b2}")
        return not any(
            True
            for delta in diff.deltas
            if Path(delta.old_file.path).parts[0] not in ("test", "tests")
            and Path(delta.new_file.path).parts[0] not in ("test", "tests")
        )

    def _add_root_node(self, node) -> Node:
        logger.debug("Adding root node {} into the graph ...", node)
        inode = self._find_identical_node(node)
        if inode is None:
            self._graph.add_node(node)
            self._repo_nodes.setdefault(node.git_url, [])
            self._repo_nodes[node.git_url].append(node)
            self._roots.add(node)
            return node
        self._add_identical_branch(inode, node.branch)
        return inode

    def _add_edge(self, node1: Node, node2: Node) -> Node:
        logger.debug("Adding edge {} -> {} into the graph ...", node1, node2)
        inode2 = self._find_identical_node(node2)
        # In the following 2 situations we need to create a new node for node2
        # 1. node2 does not have an identical node (inode2 is None)
        # 2. node2 has an identical node inode2 in the graph
        #     but inode2's parent is different from the parent of node2 (which is inode1)
        if inode2 is None:
            self._graph.add_edge(node1, node2)
            self._repo_nodes.setdefault(node2.git_url, [])
            self._repo_nodes[node2.git_url].append(node2)
            return node2
        if next(self._graph.predecessors(inode2)) != node1:
            self._graph.add_edge(node1, node2)
            return node2
        # reuse inode2
        self._add_identical_branch(inode2, node2.branch)
        return inode2

    def _add_identical_branch(self, node: Node, branch: str) -> None:
        if node.branch == branch:
            return
        self._get_identical_branches(node).add(branch)

    def _get_identical_branches(self, node: Node) -> set:
        attr = self._graph.nodes[node]
        attr.setdefault("identical_branches", set())
        return attr["identical_branches"]

    def build_graph(self):
        """Build a graph representing dependent relationships among Docker images.
        This function is called by the method build_images.
        """
        if self._graph is not None:
            return
        self._graph = nx.DiGraph()
        for branch, urls in self._branch_urls.items():
            self._build_graph_branch(branch, urls)

    def save_graph(self, output="graph.yaml") -> None:
        """Save the underlying graph structure to files."""
        with open(output, "w", encoding="utf-8") as fout:
            # nodes and attributes
            fout.write("nodes:\n")
            for node in self._graph.nodes:
                fout.write(f"  {node}: {list(self._get_identical_branches(node))}\n")
            # edges
            fout.write("edges:\n")
            for node1, node2 in self._graph.edges:
                fout.write(f"  - {node1} -> {node2}\n")
            # repos
            fout.write("repos:\n")
            for git_url, nodes in self._repo_nodes.items():
                fout.write(f"  {git_url}:\n")
                for node in nodes:
                    fout.write(f"    - {node}\n")

    def _login_servers(self) -> None:
        for server in self._servers:
            sp.run(f"docker login {server}", shell=True, check=True)

    def build_images(
        self,
        tag_build: str | None = None,
        copy_ssh_to: str = "",
        push: bool = True,
        remove: bool = False,
    ):
        """Build all Docker images in self.docker_images in order.

        :param tag_build: The tag of built images.
        :param copy_ssh_to: If True, SSH keys are copied into a directory named ssh
            under each of the local Git repositories.
        :param push: If True, push the built Docker images to DockerHub.
        :return: A pandas DataFrame summarizing building information.
        """
        self.build_graph()
        if self._builder == "docker":
            self._login_servers()
        for node in self._roots:
            self._build_images_graph(
                node=node,
                tag_build=tag_build,
                copy_ssh_to=copy_ssh_to,
                push=push,
                remove=remove,
            )
        if self.failures:
            raise DockerImageBuilderError(self._build_error_msg())

    def _build_error_msg(self):
        return (
            "Failed to build Docker images corresponding to the following nodes:\n"
            + "\n".join(
                f"{node} {list(self._get_identical_branches(node))}:\n{self._graph.nodes[node]['build_err_msg']}"
                for node in self.failures
            )
        )

    def _build_images_graph(
        self,
        node,
        tag_build: str | None,
        copy_ssh_to: str,
        push: bool,
        remove: bool,
    ) -> None:
        tags = self._gen_add_tags(tag_build, node)
        self._build_image_node(
            node=node,
            tags=tags,
            copy_ssh_to=copy_ssh_to,
            push=push,
        )
        attr = self._graph.nodes[node]
        if not attr["build_succeed"]:
            self.failures.append(node)
            return
        children = self._graph.successors(node)
        for child in children:
            self._build_images_graph(
                node=child,
                tag_build=tag_build,
                copy_ssh_to=copy_ssh_to,
                push=push,
                remove=remove,
            )
        if not remove:
            return
        # remove images associated with node
        if self._builder == "docker":
            images = docker.from_env().images
            image_name = attr["image_name"]
            for tag in tags:
                logger.info("Removing Docker image {}:{} ...", image_name, tag)
                images.remove(f"{image_name}:{tag}", force=True)

    def _build_image_node(
        self,
        node,
        tags: list[str],
        copy_ssh_to: str,
        push: bool,
    ) -> None:
        succeed, err_msg, name, tag, _, _ = DockerImage(
            git_url=node.git_url,
            branch=node.branch,
            branch_fallback=self._branch_fallback,
            repo_path=self._repo_path,
        ).build(tags=tags, copy_ssh_to=copy_ssh_to, builder=self._builder)
        attr = self._graph.nodes[node]
        attr["build_succeed"] = succeed
        attr["build_err_msg"] = err_msg
        attr["image_name"] = name
        if self._builder == "docker" and succeed and push:
            for tag in tags:
                _push_image_timing(name, tag)

    # @staticmethod
    # def _push_images(name, action_time):
    #    for idx in range(len(action_time)):
    #        tag, *_ = action_time[idx]
    #        _, *tas = _push_image_timing(name, tag)
    #        action_time.append(tas)

    def _gen_add_tags(self, tag_build, node) -> list:
        tag_build = reg_tag(tag_build, node.branch)[0]
        tags = {
            tag_build: None,
            tag_date(tag_build): None,
        }
        branches = self._graph.nodes[node].get("identical_branches", set())
        for br in branches:
            tag = branch_to_tag(br)
            tags[tag] = None
            tags[tag_date(tag)] = None
        return list(tags.keys())
