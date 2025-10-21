import unittest
import unittest.mock
import os
import tempfile
import time
from src.git import *
from src.nixos_deploy import *


# TODO: test hooks
class TestNixosDeploy(unittest.TestCase):
    def test(self) -> None:
        tmp_dir = tempfile.TemporaryDirectory()
        local_repo = f"{tmp_dir.name}/repo"
        origin_repo = f"{tmp_dir.name}/origin-repo"
        hostname = "host"

        os.mkdir(local_repo)
        os.mkdir(origin_repo)
        origin_git = GitWrapper(origin_repo)

        global config
        config = Config(
            config_dir=local_repo,
            origin_url=f"file://{origin_repo}",
            main_branch="main",
            testing_prefix="testing/",
            testing_separator="/",
            hook=None,
            main_mode=DeployModes.SWITCH,
            testing_mode=DeployModes.TEST,
            git=GitWrapper(local_repo),
        )
        nixos_deploy = NixosDeploy(config, hostname)

        origin_git.run(["init", "-b", "main"])
        nixos_deploy.setup_repo()

        testing_branch_name = f"{config.testing_prefix}{hostname}"

        def assert_chosen_commit_and_deploy(
            chosen_commit: DeployTarget,
            target_branch: str,
            branch_type: BranchType,
            is_new: bool,
            should_succeed: bool,
        ) -> None:
            target_commit = origin_git.get_commit(target_branch)
            self.assertIsNotNone(target_commit)

            # The type checker doesn't pick up the assert above
            target_commit = typing.cast(GitCommit, target_commit)

            self.assertEqual(chosen_commit.commit, target_commit)
            self.assertEqual(chosen_commit.is_new, is_new)
            self.assertEqual(chosen_commit.branch, f"origin/{target_branch}")
            self.assertEqual(chosen_commit.branch_type, branch_type)

            with unittest.mock.patch(
                "src.nixos_deploy.NixosDeploy.nixos_rebuild"
            ) as mock_function:

                def side_effect_check_commit(mode: NixosRebuildMode, flake_uri: str):
                    existing_commit = config.git.get_commit("HEAD")
                    self.assertIsNotNone(existing_commit)
                    self.assertEqual(existing_commit, chosen_commit.commit)
                    return should_succeed

                mock_function.side_effect = side_effect_check_commit

                mode = config.get_deploy_mode(branch_type)
                rebuild_mode = {
                    DeployModes.SWITCH: NixosRebuildMode.SWITCH,
                    DeployModes.TEST: NixosRebuildMode.TEST,
                }.get(mode, NixosRebuildMode.BOOT)

                nixos_deploy.deploy(target_commit, branch_type, False)
                mock_function.assert_called_once_with(
                    rebuild_mode, f"{local_repo}#{hostname}"
                )

                deployed_branch = config.git.get_commit(DEPLOYED_BRANCH)
                deployed_main_branch = config.git.get_commit(DEPLOYED_BRANCH_MAIN)
                self.assertEqual(deployed_branch, target_commit)
                if branch_type == BranchType.MAIN:
                    self.assertEqual(deployed_main_branch, target_commit)
                else:
                    self.assertNotEqual(deployed_main_branch, target_commit)

        # Test 1 - get_commit_to_deploy from main with empty local repo
        origin_git.run(["commit", "--allow-empty", "--allow-empty-message", "-m", ""])

        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit_and_deploy(
            chosen_commit, config.main_branch, BranchType.MAIN, True, True
        )

        # Test 2 - get_commit_to_deploy from main with non-empty local repo
        origin_git.run(["commit", "--allow-empty", "--allow-empty-message", "-m", ""])

        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit_and_deploy(
            chosen_commit, config.main_branch, BranchType.MAIN, True, True
        )

        # Test 3 - get_commit_to_deploy from testing
        origin_git.run(["checkout", "-b", testing_branch_name])
        origin_git.run(["commit", "--allow-empty", "--allow-empty-message", "-m", ""])

        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit_and_deploy(
            chosen_commit, testing_branch_name, BranchType.TESTING, True, True
        )

        # Test 4 - get_commit_to_deploy testing after new commit on main
        # Shouldn't switch back to the main branch
        origin_git.run(["checkout", testing_branch_name])
        origin_git.run(["commit", "--allow-empty", "--allow-empty-message", "-m", ""])

        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit_and_deploy(
            chosen_commit, testing_branch_name, BranchType.TESTING, True, True
        )

        # Test 5 - get_commit_to_deploy main after merging testing into main
        origin_git.run(["checkout", config.main_branch])
        origin_git.run(["merge", "--ff-only", testing_branch_name])

        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit_and_deploy(
            chosen_commit, config.main_branch, BranchType.MAIN, True, True
        )

        # Test 6 - check if commit is new
        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit_and_deploy(
            chosen_commit, config.main_branch, BranchType.MAIN, False, True
        )

        # Test 7 - multiple hostnames
        testing_branch_name_multiple_hostnames1 = f"{config.testing_prefix}{hostname}branch1{config.testing_separator}{hostname}"
        testing_branch_name_multiple_hostnames2 = f"{config.testing_prefix}{hostname}branch2{config.testing_separator}{hostname}"
        origin_git.run(["checkout", "-b", testing_branch_name_multiple_hostnames1])
        origin_git.run(["commit", "--allow-empty", "--allow-empty-message", "-m", ""])
        # Git timestamps have a resolution of 1 second, so we need to wait for the two commits to have different commit times
        time.sleep(2)
        origin_git.run(["checkout", config.main_branch])
        origin_git.run(["checkout", "-b", testing_branch_name_multiple_hostnames2])
        origin_git.run(["commit", "--allow-empty", "--allow-empty-message", "-m", ""])
        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit_and_deploy(
            chosen_commit,
            testing_branch_name_multiple_hostnames2,
            BranchType.TESTING,
            True,
            True,
        )

        # Test 8 - go back to main branch from testing
        origin_git.run(["checkout", config.main_branch])
        origin_git.run(["branch", "-D", testing_branch_name_multiple_hostnames1])
        origin_git.run(["branch", "-D", testing_branch_name_multiple_hostnames2])
        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit_and_deploy(
            chosen_commit, config.main_branch, BranchType.MAIN, True, True
        )


if __name__ == "__main__":
    unittest.main()
