import dataclasses
import enum
import os
import subprocess
import sys
import tomllib
import typing
from git import *


DEPLOYED_BRANCH = "_deployed"
DEPLOYED_BRANCH_MAIN = "_deployed_main"


@dataclasses.dataclass()
class Config:
    config_dir: str
    origin_url: str
    main_branch: str
    testing_prefix: str
    hook: str | None
    main_mode: "DeployModes"
    testing_mode: "DeployModes"
    git: GitWrapper

    @classmethod
    def parse(cls, path: str) -> "Config":
        with open(path, "rb") as file:
            parsed = tomllib.load(file)

        origin = parsed["origin"]
        origin_url = origin["url"]

        token = None
        if "token" in origin:
            token = origin["token"]
        elif "token_file" in origin:
            with open(origin["token_file"], "r") as file:
                token = file.readline()

        if token is not None:
            origin_url = origin_url.replace("https://", f"https://git:{token}@")

        modes = parsed.get("deploy_modes", {})
        main_mode = DeployModes(modes.get("main", DeployModes.SWITCH))
        testing_mode = DeployModes(modes.get("testing", DeployModes.TEST))

        return cls(
            config_dir=parsed["config_dir"],
            origin_url=origin_url,
            main_branch=origin["main"],
            testing_prefix=origin["testing"],
            hook=parsed.get("hook"),
            main_mode=main_mode,
            testing_mode=testing_mode,
            git=GitWrapper(parsed["config_dir"]),
        )

    def get_deploy_mode(self, branch_type: "BranchType") -> "DeployModes":
        return {
            BranchType.MAIN: self.main_mode,
            BranchType.TESTING: self.testing_mode,
        }[branch_type]


class DeployModes(enum.Enum):
    TEST = "test"
    SWITCH = "switch"
    BOOT = "boot"


class BranchType(enum.Enum):
    MAIN = "main"
    TESTING = "testing"


@dataclasses.dataclass()
class DeployTarget:
    commit: GitCommit
    branch: str
    branch_type: BranchType
    is_new: bool


class NixosDeploy:
    config: Config
    hostname: str

    def __init__(self, config: Config, hostname: str) -> None:
        self.config = config
        self.hostname = hostname

    def run_hook(
        self,
        status: typing.Literal["pre", "success", "failed"],
        branch_type: BranchType,
        mode: DeployModes,
        deploy_commit: GitCommit,
    ) -> None:
        hook_path = self.config.hook

        if hook_path is None:
            return

        hook_env = os.environ.copy()
        hook_env["DEPLOY_STATUS"] = status
        hook_env["DEPLOY_TYPE"] = branch_type.value
        hook_env["DEPLOY_MODE"] = mode.value
        hook_env["DEPLOY_COMMIT"] = deploy_commit.commit_hash

        process = subprocess.run([hook_path], env=hook_env)
        if process.returncode != 0:
            print(f"hook exited with code {process.returncode}")

    def nixos_rebuild(self, mode: DeployModes, flake_path: str) -> bool:
        """
        Wrapper around nixos-rebuild that can be mocked for testing.
        """
        args = ["nixos-rebuild", mode.value, "--flake", flake_path]
        process = subprocess.run(args, stdout=2)
        return process.returncode == 0

    def deploy(
        self, commit: GitCommit, branch_type: BranchType, magic_rollback: bool
    ) -> None:
        mode = self.config.get_deploy_mode(branch_type)
        magic_rollback = magic_rollback and (
            mode == DeployModes.SWITCH or mode == DeployModes.SWITCH
        )
        sys.stdout.flush()

        self.config.git.run(["checkout", commit.commit_hash])

        old_generation = os.path.realpath(
            "/run/current-system/bin/switch-to-configuration"
        )

        self.run_hook("pre", branch_type, mode, commit)

        if not self.nixos_rebuild(mode, f"{self.config.config_dir}#{self.hostname}"):
            print("Deployment failed")
            self.run_hook("failed", branch_type, mode, commit)
            return

        if magic_rollback:
            try:
                self.config.git.run(["fetch"])
            except GitException:
                print("No network connection - rolling back")
                process = subprocess.run([old_generation, mode.value])
                if process.returncode != 0:
                    print("Rollback failed")
                    self.run_hook("failed", branch_type, mode, commit)
                    return

                print(
                    "\nRolled back to previous generation because the network connection check failed"
                )
                self.run_hook("failed", branch_type, mode, commit)
                return

        self.config.git.reset_branch_to(DEPLOYED_BRANCH, commit)
        if branch_type == BranchType.MAIN:
            self.config.git.reset_branch_to(DEPLOYED_BRANCH_MAIN, commit)
        print(f"\nDeployment succeeded: {mode.value}")
        self.run_hook("success", branch_type, mode, commit)

    def setup_repo(self) -> None:
        if not os.path.exists(self.config.config_dir):
            os.makedirs(self.config.config_dir)

        if len(os.listdir(self.config.config_dir)) == 0:
            self.config.git.run(["init"])
            self.config.git.run(["remote", "add", "origin", self.config.origin_url])
            return

        if not os.path.exists(os.path.join(self.config.config_dir, ".git")):
            print(f"'{self.config.config_dir}' is not a git repository")
            exit(1)

        self.config.git.run(["remote", "set-url", "origin", self.config.origin_url])

    def get_commit_to_deploy(self) -> DeployTarget:
        main_branch = "origin/" + self.config.main_branch
        testing_branch = "origin/" + self.config.testing_prefix + self.hostname

        self.config.git.run(["fetch", "--prune"])

        deployed_commit = self.config.git.get_commit(DEPLOYED_BRANCH)
        deployed_main_commit = self.config.git.get_commit(DEPLOYED_BRANCH_MAIN)
        testing_commit = self.config.git.get_commit(testing_branch)
        main_commit = self.config.git.get_commit(main_branch)

        if main_commit is None:
            print(f"Error: {main_branch} does not exist")
            exit(1)

        # deployment branch is not yet initialized
        if deployed_commit is None:
            if (
                testing_commit is not None
                # testing branch is not merged into main branch
                and not self.config.git.is_ancestor(testing_commit, main_commit)
            ):
                self.config.git.run(
                    ["branch", DEPLOYED_BRANCH, testing_commit.commit_hash]
                )
                return DeployTarget(
                    testing_commit, testing_branch, BranchType.TESTING, True
                )
            self.config.git.run(["branch", DEPLOYED_BRANCH, main_commit.commit_hash])
            return DeployTarget(main_commit, main_branch, BranchType.MAIN, True)

        main_base_commit = self.config.git.get_base(deployed_commit, main_commit)
        if (
            # testing branch exists
            testing_commit is not None
            # already on the testing branch or on a former testing branch (after force-push)
            and self.config.git.is_ancestor(main_base_commit, testing_commit)
            # testing branch is not merged into main branch
            and not self.config.git.is_ancestor(testing_commit, main_commit)
        ):
            return DeployTarget(
                testing_commit,
                testing_branch,
                BranchType.TESTING,
                deployed_commit != testing_commit,
            )

        return DeployTarget(
            main_commit,
            main_branch,
            BranchType.MAIN,
            deployed_main_commit != main_commit,
        )
