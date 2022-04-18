"""Testing the module dsutil.docker.
"""
import sys
import shutil
from pathlib import Path
import pytest
import dsutil.docker

BASE_DIR = Path(__file__).parent


@pytest.mark.skipif(sys.platform == "darwin", reason="Skip test for Mac OS")
def test_images():
    if shutil.which("docker"):
        dsutil.docker.images()


@pytest.mark.skipif(sys.platform == "darwin", reason="Skip test for Mac OS")
def test_containers():
    if shutil.which("docker"):
        dsutil.docker.containers()


@pytest.mark.skipif(sys.platform == "darwin", reason="Skip test for Mac OS")
def test_remove_images():
    if shutil.which("docker"):
        dsutil.docker.remove_images(name="nimade")


def test_copy_ssh():
    builder = dsutil.docker.DockerImage(git_url="")
    builder._path = BASE_DIR
    builder._copy_ssh("ssh")


@pytest.mark.skipif(sys.platform in ("darwin", "win32"), reason="Only test on Linux")
def test_DockerImageBuilder():
    if not shutil.which("docker"):
        return
    branch_urls = {"dev": ["https://github.com/dclong/docker-python-portable.git", ]}
    builder = dsutil.docker.DockerImageBuilder(branch_urls)
    builder.build_images(tag_build="unittest", push=False)
