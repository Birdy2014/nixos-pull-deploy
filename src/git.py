import subprocess
import os


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


class GitWrapper:
    directory: str

    def __init__(self, directory: str) -> None:
        self.directory = directory

    def run(self, command: list[str]) -> str:
        full_command = ["git", "-C", self.directory] + command
        process_env = os.environ.copy()
        process_env["GIT_CONFIG_GLOBAL"] = ""
        process_env["GIT_CONFIG_SYSTEM"] = ""
        process_env["GIT_AUTHOR_NAME"] = "deploy user"
        process_env["GIT_AUTHOR_EMAIL"] = "deploy-user@localhost"
        process_env["GIT_COMMITTER_NAME"] = "deploy user"
        process_env["GIT_COMMITTER_EMAIL"] = "deploy-user@localhost"
        process = subprocess.run(full_command, capture_output=True, env=process_env)
        if process.returncode != 0:
            raise GitException(process.returncode, full_command)
        return process.stdout.decode("utf-8").strip()

    def get_commit(self, branch: str) -> GitCommit | None:
        try:
            return GitCommit(self.run(["rev-parse", branch]))
        except GitException:
            # branch does not exist
            return None

    def get_commit_message(self, commit: GitCommit) -> str:
        body = self.run([ "rev-list", "--format=%B", "--max-count=1", commit.commit_hash ]).strip()
        return "\n".join(body.split("\n")[1:])

    def is_ancestor(self, possible_ancestor: GitCommit, commit: GitCommit) -> bool:
        try:
            self.run(
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

    def get_base(self, commit1: GitCommit, commit2: GitCommit) -> GitCommit:
        return GitCommit(
            self.run(["merge-base", commit1.commit_hash, commit2.commit_hash])
        )

    def reset_branch_to(self, branch: str, target: GitCommit) -> None:
        try:
            self.run(["checkout", branch])
        except GitException as exception:
            if exception.code == 1:
                self.run(["branch", branch, target.commit_hash])
        else:
            self.run(["reset", "--hard", target.commit_hash])

    def list_remote_branches(self) -> list[str]:
        output = self.run(
            [
                "branch",
                "--list",
                "--remote",
                "--sort=-committerdate",
                "--format",
                "%(refname:short)",
            ]
        )
        return list(
            filter(lambda branch: branch.startswith("origin/"), output.split("\n"))
        )
