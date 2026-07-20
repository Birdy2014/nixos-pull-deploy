import dataclasses
import enum
import filecmp
import os
import subprocess
import sys
import tomllib
import typing
from .git import *
from .logger import *
from .nix import *


DEPLOYED_BRANCH = "_deployed"
DEPLOYED_BRANCH_MAIN = "_deployed_main"
DEPLOYED_BRANCH_SUCCESS = "_deployed_success"


@dataclasses.dataclass()
class Config:
    config_dir: str
    origin_url: str
    main_branch: str
    testing_prefix: str
    testing_separator: str
    hook: str | None
    main_mode: "BranchDeployModes"
    testing_mode: "BranchDeployModes"
    magic_rollback_timeout: int
    fetch_retries: int
    git: GitWrapper

    @classmethod
    def parse(cls, path: str, ask_token: bool) -> "Config":
        with open(path, "rb") as file:
            parsed = tomllib.load(file)

        origin = parsed["origin"]
        origin_url = origin["url"]

        token = None
        if "token" in origin:
            token = origin["token"]
        elif "token_file" in origin:
            try:
                with open(origin["token_file"], "r") as file:
                    token = file.readline()
            except (FileNotFoundError, PermissionError) as exception:
                if not ask_token:
                    raise exception
                token = input("Git token: ")

        if token is not None:
            origin_url = origin_url.replace("https://", f"https://git:{token}@")

        modes: dict[str, dict[str, str]] = parsed["deploy_modes"]

        return cls(
            config_dir=parsed["config_dir"],
            origin_url=origin_url,
            main_branch=origin["main"],
            testing_prefix=origin["testing_prefix"],
            testing_separator=origin["testing_separator"],
            hook=parsed.get("hook"),
            main_mode=BranchDeployModes.parse(modes["main"]),
            testing_mode=BranchDeployModes.parse(modes["testing"]),
            magic_rollback_timeout=parsed["magic_rollback_timeout"],
            fetch_retries=parsed["fetch_retries"],
            git=GitWrapper(parsed["config_dir"]),
        )

    def get_deploy_mode(
        self, branch_type: "BranchType", inhibition: "InhibitionStatus"
    ) -> "DeployModes":
        return {
            BranchType.MAIN: self.main_mode.get(inhibition),
            BranchType.TESTING: self.testing_mode.get(inhibition),
        }[branch_type]


class DeployModes(enum.Enum):
    TEST = "test"
    SWITCH = "switch"
    BOOT = "boot"
    REBOOT = "reboot"


class InhibitionStatus(enum.Enum):
    NORMAL = "normal"
    KERNEL_CHANGED = "kernel_changed"
    INHIBITED = "inhibited"


@dataclasses.dataclass
class BranchDeployModes:
    normal: DeployModes
    kernel_changed: DeployModes
    inhibited: DeployModes

    @classmethod
    def parse(cls, config: dict[str, str]) -> "BranchDeployModes":
        return cls(
            normal=DeployModes(config["normal"]),
            kernel_changed=DeployModes(config["kernel_changed"]),
            inhibited=DeployModes(config["inhibited"]),
        )

    def get(self, inhibition: InhibitionStatus) -> DeployModes:
        match inhibition:
            case InhibitionStatus.NORMAL:
                return self.normal
            case InhibitionStatus.KERNEL_CHANGED:
                return self.kernel_changed
            case InhibitionStatus.INHIBITED:
                return self.inhibited


class SwitchToConfigurationMode(enum.Enum):
    TEST = "test"
    SWITCH = "switch"
    BOOT = "boot"


class BranchType(enum.Enum):
    MAIN = "main"
    TESTING = "testing"


@dataclasses.dataclass()
class DeployTarget:
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
        mode: DeployModes | None,
        deploy_commit: GitCommit,
    ) -> None:
        hook_path = self.config.hook

        if hook_path is None:
            return

        deploy_success_commit = self.config.git.get_commit(DEPLOYED_BRANCH_SUCCESS)

        hook_env = os.environ.copy()
        hook_env["DEPLOY_STATUS"] = status
        hook_env["DEPLOY_TYPE"] = branch_type.value
        hook_env["DEPLOY_MODE"] = mode.value if mode is not None else ""
        hook_env["DEPLOY_COMMIT"] = deploy_commit.commit_hash
        hook_env["DEPLOY_COMMIT_MESSAGE"] = self.config.git.get_commit_message(
            deploy_commit
        )
        hook_env["DEPLOY_SUCCESS_COMMIT"] = (
            deploy_success_commit.commit_hash
            if deploy_success_commit is not None
            else ""
        )
        hook_env["DEPLOY_SUCCESS_COMMIT_MESSAGE"] = (
            self.config.git.get_commit_message(deploy_success_commit)
            if deploy_success_commit is not None
            else ""
        )
        hook_env["DEPLOY_SCHEDULED"] = "1" if os.getppid() == 1 else "0"

        process = subprocess.run([hook_path], env=hook_env)
        if process.returncode != 0:
            log(f"hook exited with code {process.returncode}", LogLevel.ERROR)

    def build_configuration(self) -> str:
        system_path = f'{self.config.config_dir}#nixosConfigurations."{self.hostname}".config.system.build.toplevel'
        build_output = nix_build(system_path)
        return build_output

    def switch_to_configuration(
        self,
        toplevel_derivation: str,
        mode: SwitchToConfigurationMode,
        install_bootloader: bool,
    ) -> bool:
        # based on nixos-rebuild
        command = [
            "systemd-run",
            "-E",
            "LOCALE_ARCHIVE",  # Will be set to new value early in switch-to-configuration script, but interpreter starts out with old value
            "-E",
            f"NIXOS_INSTALL_BOOTLOADER={"1" if install_bootloader else "0"}",
            "-E",
            "NIXOS_NO_CHECK=1",
            "--collect",
            "--no-ask-password",
            "--pipe",
            "--quiet",
            "--service-type=exec",
            "--unit=nixos-pull-deploy-switch-to-configuration",
            "--wait",
            f"{toplevel_derivation}/bin/switch-to-configuration",
            mode.value,
        ]

        process = subprocess.run(command)
        return process.returncode == 0

    def get_inhibition(self, new_configuration: str) -> InhibitionStatus:
        def paths_differ(path: str) -> bool:
            return os.path.realpath(f"/run/booted-system/{path}") != os.path.realpath(
                f"{new_configuration}/{path}"
            )

        if not filecmp.cmp(
            "/run/booted-system/switch-inhibitors",
            f"{new_configuration}/switch-inhibitors",
        ):
            return InhibitionStatus.INHIBITED

        if any(map(paths_differ, ["kernel", "kernel-modules", "initrd"])):
            return InhibitionStatus.KERNEL_CHANGED

        return InhibitionStatus.NORMAL

    def deploy(
        self,
        branch: str,
        branch_type: BranchType,
        magic_rollback: bool,
        deploy_mode_override: DeployModes | None,
    ) -> None:
        commit = self.config.git.get_commit(branch)
        if not commit:
            log(f"No commit on branch {branch}", LogLevel.ERROR)
            exit(1)

        self.config.git.run(["checkout", "--force", commit.commit_hash])

        log(f"Building {commit} on branch {branch}")
        log(self.config.git.get_commit_message(commit))
        log("")  # print newline
        sys.stdout.flush()

        self.run_hook("pre", branch_type, None, commit)

        build_output = None
        try:
            build_output = self.build_configuration()
        except NixException as exception:
            if exception.state == CommandState.CANCELLED:
                log("nix build was cancelled", LogLevel.WARNING)
                # do not run hook
                # do not set DEPLOYED_BRANCH: deployment can be retried
                return

        # set deployed branch early to prevent rebuilding a broken configuration
        self.config.git.reset_branch_to(DEPLOYED_BRANCH, commit)
        if branch_type == BranchType.MAIN:
            self.config.git.reset_branch_to(DEPLOYED_BRANCH_MAIN, commit)

        if build_output is None:
            log("Build failed", LogLevel.ERROR)
            self.run_hook("failed", branch_type, None, commit)
            return

        inhibition = self.get_inhibition(build_output)
        mode = (
            self.config.get_deploy_mode(branch_type, inhibition)
            if deploy_mode_override is None
            else deploy_mode_override
        )

        log(f"Deploying with mode {mode}, {inhibition}")
        log("")  # print newline
        sys.stdout.flush()

        if mode != DeployModes.TEST:
            nix_set_system_profile(build_output)

        old_generation = os.path.realpath("/run/current-system")

        switch_mode = {
            DeployModes.SWITCH: SwitchToConfigurationMode.SWITCH,
            DeployModes.TEST: SwitchToConfigurationMode.TEST,
        }.get(mode, SwitchToConfigurationMode.BOOT)

        if not self.switch_to_configuration(build_output, switch_mode, False):
            log("switch-to-configuration failed", LogLevel.ERROR)
            self.run_hook("failed", branch_type, mode, commit)
            return

        should_reboot = False

        if mode == DeployModes.BOOT:
            magic_rollback = False

        if mode == DeployModes.REBOOT:
            should_reboot = True
            magic_rollback = False

        if magic_rollback:
            try:
                self.config.git.fetch(self.config.magic_rollback_timeout)
            except GitException:
                log("No network connection - rolling back", LogLevel.ERROR)
                if not self.switch_to_configuration(
                    old_generation,
                    (
                        SwitchToConfigurationMode.SWITCH
                        if mode == DeployModes.SWITCH
                        else SwitchToConfigurationMode.TEST
                    ),
                    False,
                ):
                    log("Rollback failed", LogLevel.ERROR)
                    self.run_hook("failed", branch_type, mode, commit)
                    return

                log(
                    "\nRolled back to previous generation because the network connection check failed",
                    LogLevel.ERROR,
                )
                self.run_hook("failed", branch_type, mode, commit)
                return

        self.config.git.reset_branch_to(DEPLOYED_BRANCH_SUCCESS, commit)

        log(f"\nDeployment succeeded: {mode.value}")
        self.run_hook("success", branch_type, mode, commit)

        if should_reboot:
            log("Rebooting in 1 minute")
            subprocess.run(["systemctl", "reboot", "--when=+1min"])

    def setup_repo(self) -> None:
        if not os.path.exists(self.config.config_dir):
            os.makedirs(self.config.config_dir)

        if len(os.listdir(self.config.config_dir)) == 0:
            self.config.git.run(["init"])
            self.config.git.run(["remote", "add", "origin", self.config.origin_url])
            return

        if not os.path.exists(os.path.join(self.config.config_dir, ".git")):
            log(f"'{self.config.config_dir}' is not a git repository", LogLevel.ERROR)
            exit(1)

        self.config.git.run(["remote", "set-url", "origin", self.config.origin_url])

    def get_commit_to_deploy(self) -> DeployTarget:
        main_branch = "origin/" + self.config.main_branch

        self.config.git.fetch(self.config.fetch_retries)

        def filter_hostname_branch(branch: str) -> bool:
            if not branch.startswith(f"origin/{self.config.testing_prefix}"):
                return False
            hostnames = branch[
                len("origin/") + len(self.config.testing_prefix) :
            ].split(self.config.testing_separator)
            return self.hostname in hostnames

        testing_branches = list(
            filter(filter_hostname_branch, self.config.git.list_remote_branches())
        )

        deployed_commit = self.config.git.get_commit(DEPLOYED_BRANCH)
        deployed_main_commit = self.config.git.get_commit(DEPLOYED_BRANCH_MAIN)
        main_commit = self.config.git.get_commit(main_branch)

        if main_commit is None:
            log(f"Error: {main_branch} does not exist", LogLevel.ERROR)
            exit(1)

        if len(testing_branches) > 1:
            log(
                f"Warning: found {len(testing_branches)} testing branches targeting this host:\n{"\n".join(map(lambda branch: f"- {branch}", testing_branches))}",
                LogLevel.WARNING,
            )

        for testing_branch in testing_branches:
            testing_commit = self.config.git.get_commit(testing_branch)
            if testing_commit is None:
                continue

            is_suitable, is_new = self.is_testing_commit_suitable_and_new(
                testing_commit
            )
            if is_suitable:
                return DeployTarget(testing_branch, BranchType.TESTING, is_new)

        # deployment branch is not yet initialized
        if deployed_commit is None:
            self.config.git.run(["branch", DEPLOYED_BRANCH, main_commit.commit_hash])
            return DeployTarget(main_branch, BranchType.MAIN, True)

        return DeployTarget(
            main_branch,
            BranchType.MAIN,
            deployed_commit != main_commit or deployed_main_commit != main_commit,
        )

    def is_testing_commit_suitable_and_new(
        self, testing_commit: GitCommit
    ) -> tuple[bool, bool]:
        main_branch = "origin/" + self.config.main_branch

        deployed_commit = self.config.git.get_commit(DEPLOYED_BRANCH)
        main_commit = self.config.git.get_commit(main_branch)

        if main_commit is None:
            log(f"Error: {main_branch} does not exist", LogLevel.ERROR)
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
                return True, True
            return False, False

        main_base_commit = self.config.git.get_base(deployed_commit, main_commit)
        return (
            # testing branch exists
            testing_commit is not None
            # already on the testing branch or on a former testing branch (after force-push)
            and self.config.git.is_ancestor(main_base_commit, testing_commit)
            # testing branch is not merged into main branch
            and not self.config.git.is_ancestor(testing_commit, main_commit)
        ), deployed_commit != testing_commit
