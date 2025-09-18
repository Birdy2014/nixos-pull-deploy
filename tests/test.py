import unittest
import unittest.mock
import os
import tempfile
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
            testing_prefix="testing-",
            hook=None,
            git=GitWrapper(local_repo),
        )
        nixos_deploy = NixosDeploy(config, hostname)

        origin_git.run(["init", "-b", "main"])
        nixos_deploy.setup_repo()

        testing_branch_name = f"{config.testing_prefix}{hostname}"

        def assert_chosen_commit(
            chosen_commit: DeployTarget,
            target_branch: str,
            branch_type: BranchType,
            is_new: bool,
        ) -> None:
            target_commit = origin_git.get_commit(target_branch)
            self.assertIsNotNone(target_commit)

            self.assertEqual(chosen_commit.commit, target_commit)
            self.assertEqual(chosen_commit.is_new, is_new)
            self.assertEqual(chosen_commit.branch, f"origin/{target_branch}")
            self.assertEqual(chosen_commit.branch_type, branch_type)

        def run_mocked_deploy(
            target_commit: GitCommit, mode: DeployModes, should_succeed: bool
        ):
            with unittest.mock.patch(
                "src.nixos_deploy.NixosDeploy.nixos_rebuild"
            ) as mock_function:

                def side_effect_check_commit(mode: DeployModes, flake_uri: str):
                    existing_commit = config.git.get_commit("HEAD")
                    self.assertIsNotNone(existing_commit)
                    self.assertEqual(existing_commit, chosen_commit.commit)
                    return should_succeed

                mock_function.side_effect = side_effect_check_commit

                nixos_deploy.deploy(target_commit, mode, False)
                mock_function.assert_called_once_with(mode, f"{local_repo}#{hostname}")

                deployed_branch = config.git.get_commit(DEPLOYED_BRANCH)
                self.assertEqual(deployed_branch, target_commit)

                target_status = (
                    DeployStatus.from_success_mode(mode)
                    if should_succeed
                    else DeployStatus.FAILED
                )
                self.assertEqual(
                    config.git.get_note(target_commit), target_status.value
                )

        # Test 1 - get_commit_to_deploy from main with empty local repo
        origin_git.run(["commit", "--allow-empty", "--allow-empty-message", "-m", ""])

        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit(chosen_commit, config.main_branch, BranchType.MAIN, True)

        run_mocked_deploy(chosen_commit.commit, DeployModes.SWITCH, True)

        # Test 2 - get_commit_to_deploy from main with non-empty local repo
        origin_git.run(["commit", "--allow-empty", "--allow-empty-message", "-m", ""])

        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit(chosen_commit, config.main_branch, BranchType.MAIN, True)

        run_mocked_deploy(chosen_commit.commit, DeployModes.SWITCH, True)

        # Test 3 - get_commit_to_deploy from testing
        origin_git.run(["checkout", "-b", testing_branch_name])
        origin_git.run(["commit", "--allow-empty", "--allow-empty-message", "-m", ""])

        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit(
            chosen_commit, testing_branch_name, BranchType.TESTING, True
        )

        run_mocked_deploy(chosen_commit.commit, DeployModes.SWITCH, True)

        # Test 4 - get_commit_to_deploy testing after new commit on main
        # Shouldn't switch back to the main branch
        origin_git.run(["checkout", testing_branch_name])
        origin_git.run(["commit", "--allow-empty", "--allow-empty-message", "-m", ""])

        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit(
            chosen_commit, testing_branch_name, BranchType.TESTING, True
        )

        run_mocked_deploy(chosen_commit.commit, DeployModes.TEST, True)

        # Test 5 - get_commit_to_deploy main after merging testing into main
        origin_git.run(["checkout", config.main_branch])
        origin_git.run(["merge", "--ff-only", testing_branch_name])

        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit(chosen_commit, config.main_branch, BranchType.MAIN, True)

        run_mocked_deploy(chosen_commit.commit, DeployModes.SWITCH, True)

        # Test 6 - check if commit is new
        chosen_commit = nixos_deploy.get_commit_to_deploy()
        assert_chosen_commit(chosen_commit, config.main_branch, BranchType.MAIN, False)

        run_mocked_deploy(chosen_commit.commit, DeployModes.SWITCH, True)


if __name__ == "__main__":
    unittest.main()
