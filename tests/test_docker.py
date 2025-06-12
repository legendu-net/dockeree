"""Test tools for managing Docker images and containers."""

import sys
import shutil
from pathlib import Path
import pytest
import dockeree

BASE_DIR = Path(__file__).parent


@pytest.mark.skipif(sys.platform == "darwin", reason="Skip test for Mac OS")
def test_images():
    if shutil.which("docker"):
        dockeree.images()


@pytest.mark.skipif(sys.platform == "darwin", reason="Skip test for Mac OS")
def test_containers():
    if shutil.which("docker"):
        dockeree.containers()


@pytest.mark.skipif(sys.platform == "darwin", reason="Skip test for Mac OS")
def test_remove_images():
    if shutil.which("docker"):
        dockeree.remove_images(name="nimade")


def test_copy_ssh():
    builder = dockeree.DockerImage(git_url="")
    builder._path = BASE_DIR
    builder._copy_ssh("ssh")
