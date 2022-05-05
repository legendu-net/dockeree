import os
import sys
from pathlib import Path
import subprocess as sp
from argparse import Namespace, ArgumentParser
from loguru import logger


def _get_port(image_name):
    if not image_name.startswith("dclong/"):
        return None
    image_name = image_name[7:]
    if image_name.startswith("jupyterlab"):
        return 8888
    if image_name.startswith("jupyterhub"):
        return 8000
    if image_name.startswith("vscode"):
        return 8080


def launch(args):
    cmd = [
        "docker", "run", "-d", "--init",
        "--log-opt", "max-size=50m",
        "-e", "DOCKER_USER=$(id -un)",
        "-e", "DOCKER_USER_ID=$(id -u)",
        "-e", "DOCKER_PASSWORD=$(id -un)",
        "-e", "DOCKER_GROUP_ID=$(id -g)",
        "-e", "DOCKER_ADMIN_USER=$(id -un)",
        "-v", f"{os.getcwd()}:/workdir",
        "-v", f"{Path.home().parent}:/home_host",
        "--hostname", args.image_name[args.image_name.find("/") + 1 : args.image_name.find(":")],
    ]
    if sys.platform == "linux":
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        memory = int(memory * 0.8)
        cmd.append(f"--memory={memory}b")
        cpus = max(os.cpu_count() - 1, 1)
        cmd.append(f"--cpus={cpus}")
    port = _get_port(args.image_name)
    if port:
        cmd.append(f"-p {args.port if args.port else port}:{port}")
    if args.extra_port_mappings:
        cmd.extend("-p " + mapping for mapping in args.extra_port_mappings)
    cmd.append(args.image_name)
    if args.image_name.startswith("dclong/"):
        cmd.append("/scripts/sys/init.sh")
    cmd = " ".join(cmd)
    logger.debug("Launching Docker container using the following command:\n{}", cmd)
    sp.run(cmd, shell=True)


def parse_args(args=None, namespace=None) -> Namespace:
    """Parse command-line arguments.
    
    :param args: The arguments to parse. 
        If None, the arguments from command-line are parsed.
    :param namespace: An inital Namespace object.
    :return: A namespace object containing parsed options.
    """
    parser = ArgumentParser(
        description="Launch Docker containers quickly."
    )
    parser.add_argument(
        "image_name", 
        help="The name (including tag) of the Docker image to launch."
    )
    parser.add_argument(
        "-p",
        "--port",
        dest="port",
        required=True,
        help="The port on the host machine (to map to the port inside the container)."
    )
    parser.add_argument(
        "--extra-port-mappings",
        dest="extra_port_mappings",
        nargs="*",
        default=(),
        help="Extra port mappings."
    )
    args = parser.parse_args(args=args, namespace=namespace)
    return args


def main():
    """Run launch command-line interface.
    """
    args = parse_args()
    launch(args)


if __name__ == "__main__":
    main()
