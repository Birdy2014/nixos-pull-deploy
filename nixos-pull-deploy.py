#!/usr/bin/env python

import argparse
import enum
import os
import subprocess
import tomllib
import typing


DEPLOYED_BRANCH = "_deployed"


class Config:
    config_dir: str
    origin_url: str
    main_branch: str
    testing_prefix: str
    origin_url: str
    hooks: dict[str, str]

    def __init__(self, path: str) -> None:
        with open(path, "rb") as file:
            parsed = tomllib.load(file)

        self.config_dir = parsed["config_dir"]
        self.hooks = parsed["hooks"]
        origin = parsed["origin"]
        self.origin_url = origin["url"]
        self.main_branch = origin["main"]
        self.testing_prefix = origin["testing"]

        token = None
        if "token" in origin:
            token = origin["token"]
        elif "token_file" in origin:
            with open(origin["token_file"], "r") as file:
                token = file.readline()

        if token is not None:
            self.origin_url = self.origin_url.replace(
                "https://", f"https://git:{token}@"
            )


class GitCommit:
    commit_hash: str

    def __init__(self, commit_hash: str) -> None:
        self.commit_hash = commit_hash

    def __repr__(self) -> str:
        return self.commit_hash

    def __eq__(self, value: object, /) -> bool:
        return isinstance(value, GitCommit) and self.commit_hash == value.commit_hash


class GitException(Exception):
    code: int

    def __init__(self, code: int, command: list[str]) -> None:
        self.code = code
        super().__init__(f"Git failed with code {code}\ncommand: {" ".join(command)}")


class DeployModes(enum.Enum):
    TEST = "test"
    SWITCH = "switch"
    DRY_ACTIVATE = "dry-activate"


def git_command(command: list[str]) -> str:
    full_command = ["git", "-C", config.config_dir] + command
    process = subprocess.run(full_command, capture_output=True)
    if process.returncode != 0:
        raise GitException(process.returncode, full_command)
    return process.stdout.decode("utf-8").strip()


def git_get_commit(branch: str) -> GitCommit | None:
    try:
        return GitCommit(git_command(["rev-parse", branch]))
    except GitException:
        # branch does not exist
        return None


def git_is_ancestor(possible_ancestor: GitCommit, commit: GitCommit) -> bool:
    try:
        git_command(
            [
                "merge-base",
                "--is-ancestor",
                possible_ancestor.commit_hash,
                commit.commit_hash,
            ]
        )
        return True
    except GitException as exception:
        if exception.code == 1:
            return False
        raise exception


def git_get_base(commit1: GitCommit, commit2: GitCommit) -> GitCommit:
    return GitCommit(
        git_command(["merge-base", commit1.commit_hash, commit2.commit_hash])
    )


def run_hook(hook_type: typing.Literal["success", "error"]) -> None:
    hook_path = config.hooks[hook_type]

    if hook_path is None:
        return

    process = subprocess.run([hook_path])
    if process.returncode != 0:
        print(f"error hook exited with code {process.returncode}")


def deploy(commit: GitCommit, mode: DeployModes, magic_rollback: bool) -> None:
    git_command(["checkout", commit.commit_hash])

    old_generation = os.path.realpath("/run/current-system/bin/switch-to-configuration")

    args = ["nixos-rebuild", mode.value, "--flake", f"{config.config_dir}#{hostname}"]
    process = subprocess.run(args, stdout=2)
    if process.returncode != 0:
        print("Deployment failed")
        run_hook("error")
        return

    if magic_rollback:
        try:
            git_command(["fetch"])
        except GitException:
            print("No network connection - rolling back")
            process = subprocess.run([old_generation, "switch"])
            if process.returncode != 0:
                print("Rollback failed")
                run_hook("error")
                return

            print(
                "\nRolled back to previous generation because the network connection check failed"
            )
            return

    git_command(["checkout", DEPLOYED_BRANCH])
    git_command(["reset", "--hard", commit.commit_hash])
    print("\nDeployment succeeded")
    run_hook("success")


def setup_repo() -> None:
    if not os.path.exists(config.config_dir):
        os.makedirs(config.config_dir)

    if len(os.listdir(config.config_dir)) == 0:
        git_command(["init"])
        git_command(["remote", "add", "origin", config.origin_url])
        return

    if not os.path.exists(os.path.join(config.config_dir, ".git")):
        print(f"'{config.config_dir}' is not a git repository")
        exit(1)

    git_command(["remote", "set-url", "origin", config.origin_url])


def get_commit_to_deploy(
    force_rebuild: bool,
) -> tuple[GitCommit, str, DeployModes] | str:
    """
    returns tuple if a new commit is available, string with current branch name otherwise
    """
    main_branch = "origin/" + config.main_branch
    testing_branch = "origin/" + config.testing_prefix + hostname

    git_command(["fetch", "--prune"])

    deployed_commit = git_get_commit(DEPLOYED_BRANCH)
    testing_commit = git_get_commit(testing_branch)
    main_commit = git_get_commit(main_branch)

    if main_commit is None:
        print(f"Error: {main_branch} does not exist")
        exit(1)

    # deployment branch is not yet initialized
    if deployed_commit is None:
        if (
            testing_commit is not None
            # testing branch is not merged into main branch
            and not git_is_ancestor(testing_commit, main_commit)
        ):
            git_command(["branch", DEPLOYED_BRANCH, testing_commit.commit_hash])
            return testing_commit, testing_branch, DeployModes.TEST
        git_command(["branch", DEPLOYED_BRANCH, main_commit.commit_hash])
        return main_commit, main_branch, DeployModes.SWITCH

    main_base_commit = git_get_base(deployed_commit, main_commit)
    if (
        # testing branch exists
        testing_commit is not None
        # already on the testing branch or on a former testing branch (after force-push)
        and git_is_ancestor(main_base_commit, testing_commit)
        # testing branch is not merged into main branch
        and not git_is_ancestor(testing_commit, main_commit)
    ):
        if force_rebuild or deployed_commit != testing_commit:
            return testing_commit, testing_branch, DeployModes.TEST
        return testing_branch

    if force_rebuild or deployed_commit != main_commit:
        return main_commit, main_branch, DeployModes.SWITCH

    return main_branch


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


def action_run(force_rebuild: bool, dry_run: bool, magic_rollback: bool) -> None:
    if is_rebuilding():
        print("A rebuild is already running")
        return

    value = get_commit_to_deploy(force_rebuild)
    if isinstance(value, str):
        print(f"Already on newest {value} commit")
        return

    commit, branch, mode = value
    print(f"Deploying {branch}, {commit} mode {mode}")
    deploy(commit, DeployModes.DRY_ACTIVATE if dry_run else mode, magic_rollback)


def action_check() -> None:
    value = get_commit_to_deploy(False)
    if isinstance(value, str):
        print(f"Already on newest {value} commit")
        return

    commit, branch, mode = value
    print(f"New commit available on {branch}: {commit}")


# TODO: cli: (maybe additionally to having a pidfile, check whether a nixos-rebuild process runs)
# - status -> show if running, success, failed (check pidfile, git hash in /run/current-system?)
# - cancel -> check pidfile, INT process

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config", required=True)

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
    subparser_run.add_argument("-d", "--dry-run", action="store_true")
    subparser_run.add_argument("--magic-rollback", action="store_true", default=True)

    subparser_check = subparsers.add_parser("check", help="check for new commits")

    args = parser.parse_args()

    config = Config(args.config)
    hostname = os.uname().nodename

    match args.action:
        case "run":
            setup_repo()
            action_run(args.rebuild, args.dry_run, args.magic_rollback)
        case "check":
            action_check()
