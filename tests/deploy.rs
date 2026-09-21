mod common;

use common::{Call, TestEnv};
use nixos_pull_deploy::{
    config::{DEPLOYED_BRANCH, DEPLOYED_BRANCH_MAIN, DEPLOYED_BRANCH_SUCCESS, DeployMode},
    system::{BranchType, HookStatus, SwitchMode},
};

#[test]
fn build_failure_runs_failed_hook_without_switching() {
    let env = TestEnv::new();
    let main = env.commit("main", 100, &[]);

    let target = env.deployer.get_commit_to_deploy().unwrap();
    assert_eq!(target.branch, "origin/main");

    env.mock.set_build_ok(false);
    let target_branch_type = target.branch_type(&env.deployer.config);
    let result = env
        .deployer
        .deploy(&target.branch, target_branch_type, false, None);
    assert!(result.is_err());

    assert_eq!(
        &env.mock.calls(),
        &vec![
            Call::Hook(HookStatus::Pre, BranchType::Main, None, main),
            Call::Build(env.toplevel_installable()),
            Call::Hook(HookStatus::Failed, BranchType::Main, None, main),
        ]
    );

    assert_eq!(env.deployer.git.get_commit(DEPLOYED_BRANCH), Some(main));
    assert_eq!(
        env.deployer.git.get_commit(DEPLOYED_BRANCH_MAIN),
        Some(main)
    );
    assert_eq!(env.deployer.git.get_commit(DEPLOYED_BRANCH_SUCCESS), None);
}

#[test]
fn cancelled_build_is_retryable_without_hook_or_ref_updates() {
    let env = TestEnv::new();
    let main = env.commit("main", 100, &[]);

    let target = env.deployer.get_commit_to_deploy().unwrap();

    env.mock.set_build_cancelled(true);
    let target_branch_type = target.branch_type(&env.deployer.config);
    let result = env
        .deployer
        .deploy(&target.branch, target_branch_type, false, None);
    assert!(result.is_err());

    // A cancelled build runs no hook and performs no switch.
    assert_eq!(
        &env.mock.calls(),
        &vec![
            Call::Hook(HookStatus::Pre, BranchType::Main, None, main),
            Call::Build(env.toplevel_installable())
        ]
    );
    // No bookkeeping ref is advanced: the deployment can be retried.
    assert_eq!(env.deployer.git.get_commit(DEPLOYED_BRANCH), Some(main));
    assert_eq!(env.deployer.git.get_commit(DEPLOYED_BRANCH_MAIN), None);
    assert_eq!(env.deployer.git.get_commit(DEPLOYED_BRANCH_SUCCESS), None);
}

#[test]
fn magic_rollback_success() {
    for mode in [Some(DeployMode::Test), Some(DeployMode::Switch), None] {
        let mode = mode.unwrap_or(DeployMode::Switch);

        let env = TestEnv::new();
        let main = env.commit("main", 100, &[]);

        let target = env.deployer.get_commit_to_deploy().unwrap();

        let target_branch_type = target.branch_type(&env.deployer.config);
        let result = env
            .deployer
            .deploy(&target.branch, target_branch_type, true, Some(mode));
        assert!(result.is_ok());

        let mut expected_calls = vec![
            Call::Hook(HookStatus::Pre, BranchType::Main, None, main),
            Call::Build(env.toplevel_installable()),
            Call::Switch(env.mock.build_output.borrow().clone(), SwitchMode::Test),
        ];
        if mode != DeployMode::Test {
            expected_calls.extend(vec![
                Call::Profile(env.mock.build_output.borrow().clone()),
                Call::Switch(env.mock.build_output.borrow().clone(), SwitchMode::Boot),
            ]);
        }
        expected_calls.push(Call::Hook(
            HookStatus::Success,
            BranchType::Main,
            Some(mode),
            main,
        ));

        assert_eq!(&env.mock.calls(), &expected_calls);

        assert_eq!(env.deployer.git.get_commit(DEPLOYED_BRANCH), Some(main));
        assert_eq!(
            env.deployer.git.get_commit(DEPLOYED_BRANCH_MAIN),
            Some(main)
        );
        assert_eq!(
            env.deployer.git.get_commit(DEPLOYED_BRANCH_SUCCESS),
            Some(main)
        );
    }
}

#[test]
fn magic_rollback_on_fetch_failure() {
    let env = TestEnv::new();
    let main = env.commit("main", 100, &[]);

    let target = env.deployer.get_commit_to_deploy().unwrap();

    // The post-switch connectivity check cannot succeed.
    env.remove_origin();

    let target_branch_type = target.branch_type(&env.deployer.config);
    let result = env
        .deployer
        .deploy(&target.branch, target_branch_type, true, None);
    assert!(result.is_err());

    // Test-switch the new toplevel, the connectivity fetch fails, roll back
    // to the old toplevel in the deploy's switch mode, failed hook — and the
    // deploy stops there: no profile, no final switch.
    assert_eq!(
        &env.mock.calls(),
        &vec![
            Call::Hook(HookStatus::Pre, BranchType::Main, None, main),
            Call::Build(env.toplevel_installable()),
            Call::Switch(env.mock.build_output.borrow().clone(), SwitchMode::Test),
            Call::Switch(env.mock.current_toplevel.borrow().clone(), SwitchMode::Test),
            Call::Hook(
                HookStatus::Failed,
                BranchType::Main,
                Some(DeployMode::Switch),
                main
            ),
        ]
    );
    // The deploy failed: no success ref.
    assert_eq!(env.deployer.git.get_commit(DEPLOYED_BRANCH), Some(main));
    assert_eq!(
        env.deployer.git.get_commit(DEPLOYED_BRANCH_MAIN),
        Some(main)
    );
    assert_eq!(env.deployer.git.get_commit(DEPLOYED_BRANCH_SUCCESS), None);
}
