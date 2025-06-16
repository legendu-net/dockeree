"""Test DockerImageBuilder."""

import sys
import shutil
from pathlib import Path
import pytest
import dockeree

BASE_DIR = Path(__file__).parent


@pytest.mark.skipif(sys.platform in ("darwin", "win32"), reason="Only test on Linux")
def test_DockerImageBuilder():
    if not shutil.which("docker"):
        return
    branch_urls = {
        "dev": {
            "https://github.com/dclong/docker-python-portable.git": "",
        }
    }
    builder = dockeree.DockerImageBuilder(branch_urls)
    builder.build_images(tag_build="unittest", push=False)
