"""Docker related utils.
"""
from __future__ import annotations
import sys
from typing import Union
import subprocess as sp
from loguru import logger
import pandas as pd
import docker
from .builder import DockerImage, DockerImageBuilder


def _get_image_repo(image):
    repo_digests = image.attrs["RepoDigests"]
    if repo_digests:
        return repo_digests[0].split("@", maxsplit=1)[0]
    repo_tags = image.attrs["RepoTags"]
    if repo_tags:
        return repo_tags[0].split(":", maxsplit=1)[0]
    return ""


def images() -> pd.DataFrame:
    """Get information of Docker images.

    :return: A pandas DataFrame with columns: repository, tag, image_id, created and size.
    """
    data = []
    for image in docker.from_env().images.list():
        repository = _get_image_repo(image)
        image_id = image.short_id[7:]
        created = image.attrs["Created"]
        size = image.attrs["Size"]
        if image.tags:
            for tag in image.tags:
                data.append(
                    {
                        "repository": repository,
                        "tag": tag.split(":")[1],
                        "image_id": image_id,
                        "created": created,
                        "size": size
                    }
                )
        else:
            data.append(
                {
                    "repository": repository,
                    "tag": "<none>",
                    "image_id": image_id,
                    "created": created,
                    "size": size
                }
            )
    frame = pd.DataFrame(data)
    frame.created = pd.to_datetime(frame.created)
    return frame


def containers() -> pd.DataFrame:
    """Return Docker containers as a pandas DataFrame.
    """
    data = [
        (
            cont.short_id,
            cont,
            cont.image.tags[0] if cont.image.tags else cont.image.short_id[7:],
            cont.attrs["Config"]["Cmd"],
            cont.attrs["Created"],
            cont.status,
            cont.ports,
            cont.name,
        ) for cont in docker.from_env().containers.list(all=True)
    ]
    columns = [
        "container_id", "container_obj", "image", "command", "created", "status",
        "ports", "name"
    ]
    return pd.DataFrame(data, columns=columns)


def remove(aggressive: bool = False, choice: str = "") -> None:
    """Remove exited Docker containers and images without tags.
    """
    docker.from_env().containers.prune()
    failures = remove_images(tag="none", aggressive=aggressive, choice=choice)
    logger.error(
        "Failed to remove the following Docker images:\n{}", "\n".join(failures)
    )


def pull():
    """Automatically pull all valid images.
    """
    client = docker.from_env()
    imgs = images()
    imgs = imgs[imgs.repository != "<None>" & imgs.tag != "<None>"]
    for _, (repo, tag, *_) in imgs.iterrows():
        client.images.pull(repo, tag)


def remove_images(
    id_: str = "",
    name: str = "",
    tag: str = "",
    aggressive: bool = False,
    frame: Union[pd.DataFrame, None] = None,
    choice: str = ""
) -> list[str]:
    """Remove specified Docker images.
    :param id_: The id of the image to remove.
    :param name: A (regex) pattern of names of images to remove.
    :param tag: Remove images whose tags containing specified tag.
    :return: A list of images failed to be removed.
    """
    frames = []
    if frame:
        frames.append(frame)
    imgs = images()
    if id_:
        frames.append(imgs[imgs.image_id.str.contains(id_, case=False)])
    if name:
        frames.append(imgs[imgs.repository.str.contains(name, case=False)])
    if tag:
        frames.append(imgs[imgs.tag.str.contains(tag, case=False)])
    if aggressive:
        frames.append(imgs[imgs.tag.str.contains(r"[a-z]*_?\d{4,}", case=False)])
        frames.append(
            imgs[~imgs.tag.str.contains(r"\d{4,}")].groupby("image_id").apply(  # pylint: disable=E1101
            lambda frame: frame.query("tag == 'next'") if frame.shape[0] > 1 else None
        ))
    return _remove_images_frame(pd.concat(frames, ignore_index=True), choice=choice)


def _remove_images_frame(imgs, choice: str = "") -> list[str]:
    if imgs.empty:
        return []
    imgs = imgs.drop_duplicates().sort_values("created", ascending=False)
    print("\n", imgs, "\n")
    sys.stdout.flush()
    sys.stderr.flush()
    print("-" * 80)
    if not choice:
        choice = input(
            "Do you want to remove the above images? (y - Yes, n - [No], i - interactive): "
        )
    client = docker.from_env()
    failures = []
    for row in imgs.itertuples():
        image_name = row.repository + ":" + row.tag
        image = row.image_id if row.tag == "<none>" else image_name
        if choice == "y":
            try:
                client.images.remove(image)
            except:
                failures.append(image)
        elif choice == "i":
            choice_i = input(
                f"Do you want to remove the image '{image_name}'? (y - Yes, n - [No]):"
            )
            if choice_i == "y":
                try:
                    client.images.remove(image)
                except:
                    failures.append(image)
    return failures


def stop(id_: str = "", name: str = "", status: str = "", choice: str = "") -> None:
    """Stop the specified Docker containers.
    :param id_: The id of the container to remove.
    :param name: A (regex) pattern of names of containers to remove.
    :param status: Stop containers with the specified status.
    :param choice: One of "y" (auto yes), "n" (auto no)
        or "i" (interactive, i.e., ask for confirmation on each case).
    """
    client = docker.from_env()
    if id_:
        client.stop(id_)
    if name:
        client.stop(name)
    if status:
        conts = containers()
        conts = conts[conts.status.str.contains(status, case=False)]
        if conts.empty:
            return
        print("\n", conts, "\n")
        sys.stdout.flush()
        sys.stderr.flush()
        if not choice:
            choice = input(
                "Do you want to stop the above containers? (y - Yes, n - [No], i - interactive): "
            )
        for row in conts.itertuples():
            if choice == "y":
                client.stop(row.container_id)
            elif choice == "i":
                choice_i = input(
                    f"Do you want to stop the container '{row.names}'? (y/N): "
                )
                if choice_i == "y":
                    client.stop(row.container_id)
    sp.run("docker ps", shell=True, check=True)
