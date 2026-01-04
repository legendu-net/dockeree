from dataclasses import dataclass


@dataclass(frozen=True)
class Node:
    """A class similar to DockerImage for simplifying code."""

    git_url: str
    branch: str

    def __str__(self):
        rindex = self.git_url.rindex("/")
        # HTTP urls, e.g., https://github.com/dclong/docker-jupyterhub-ds
        index = self.git_url.rfind("/", 0, rindex)
        if index < 0:
            # SSH urls, e.g., git@github.com:dclong/docker-jupyterhub-ds
            index = self.git_url.rindex(":", 0, rindex)
        return self.git_url[(index + 1) :] + f"<{self.branch}>"
