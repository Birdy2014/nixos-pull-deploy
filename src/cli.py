#!/usr/bin/env python

import argparse
import os
import subprocess
from git import *
from nixos_deploy import *
from logger import *


def is_rebuilding() -> bool:
    process = subprocess.run(
        ["pgrep", "-x", "nixos-rebuild"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process.returncode == 0


def action_run(force_rebuild: bool, magic_rollback: bool) -> None:
    if is_rebuilding():
        log("A rebuild is already running", LogLevel.ERROR)
        return

    target = nixos_deploy.get_commit_to_deploy()
    if not force_rebuild and not target.is_new:
        log(f"Already on newest {target.branch} commit")
        return

    mode = nixos_deploy.config.get_deploy_mode(target.branch_type)
    log(f"Deploying {target.branch}, {target.commit} mode {mode}")
    nixos_deploy.deploy(target.commit, target.branch_type, magic_rollback)


def action_check() -> None:
    target = nixos_deploy.get_commit_to_deploy()
    if not target.is_new:
        log(f"Already on newest {target.branch} commit")
        return

    log(f"New commit available on {target.branch}: {target.commit}")


def main() -> None:
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers(required=True, dest="action")

    subparser_run = subparsers.add_parser(
        "run", help="fetch changes and rebuild if necessary"
    )
    subparser_run.add_argument(
        "-r",
        "--rebuild",
        action="store_true",
        help="Rebuild even if the build was attempted before",
    )
    subparser_run.add_argument(
        "--magic-rollback", action=argparse.BooleanOptionalAction, default=True
    )

    subparser_check = subparsers.add_parser("check", help="check for new commits")

    args = parser.parse_args()

    config_file = os.environ.get("DEPLOY_CONFIG")
    if config_file is None:
        log("Error: environment variable DEPLOY_CONFIG not set", LogLevel.ERROR)
        exit(1)

    if os.geteuid() != 0:
        log("Error: I can only run as root", LogLevel.ERROR)
        exit(1)

    config = Config.parse(config_file)
    hostname = os.uname().nodename
    global nixos_deploy
    nixos_deploy = NixosDeploy(config, hostname)

    match args.action:
        case "run":
            nixos_deploy.setup_repo()
            action_run(args.rebuild, args.magic_rollback)
        case "check":
            if not os.path.exists(config.config_dir):
                log(f"Error: Local repo does not exist. Run '{parser.prog} run' first.")
                return
            action_check()


if __name__ == "__main__":
    main()
