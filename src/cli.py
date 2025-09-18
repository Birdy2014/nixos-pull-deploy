#!/usr/bin/env python

import argparse
import os
import subprocess
from git import *
from nixos_deploy import *


def is_rebuilding() -> bool:
    process = subprocess.run(
        ["pgrep", "-x", "nixos-rebuild"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if process.returncode == 0:
        return True

    # TODO: Find out if a rebuild is currently running:
    # - check for nixos-rebuild-switch-to-configuration.service
    # - check if this script is running: pidfile?
    # - check for systemd service that starts this script
    return False


def action_run(force_rebuild: bool, magic_rollback: bool) -> None:
    if is_rebuilding():
        print("A rebuild is already running")
        return

    target = nixos_deploy.get_commit_to_deploy()
    if not force_rebuild and not target.is_new:
        print(f"Already on newest {target.branch} commit")
        return

    mode = nixos_deploy.config.get_deploy_mode(target.branch_type)
    print(f"Deploying {target.branch}, {target.commit} mode {mode}")
    nixos_deploy.deploy(target.commit, mode, magic_rollback)


def action_check() -> None:
    target = nixos_deploy.get_commit_to_deploy()
    if not target.is_new:
        print(f"Already on newest {target.branch} commit")
        return

    print(f"New commit available on {target.branch}: {target.commit}")


# TODO: cli: (maybe additionally to having a pidfile, check whether a nixos-rebuild process runs)
# - status -> show if running, success, failed (check pidfile, git hash in /run/current-system?)
# - cancel -> check pidfile, INT process


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
        print("Error: environment variable DEPLOY_CONFIG not set")
        exit(1)

    if os.geteuid() != 0:
        print("Error: I can only run as root")
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
            action_check()


if __name__ == "__main__":
    main()
