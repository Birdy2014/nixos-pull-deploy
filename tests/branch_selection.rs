mod common;

use common::TestEnv;
use nixos_pull_deploy::{
    config::{DEPLOYED_BRANCH, DEPLOYED_BRANCH_MAIN, DEPLOYED_BRANCH_SUCCESS, DeployMode},
    deploy::DeployTarget,
    git::Commit,
    system::{HookStatus, SwitchMode},
};

use crate::common::Call;

fn deploy_and_assert(
    env: &TestEnv,
    target: &DeployTarget,
    switch_mode: SwitchMode,
    deploy_mode: DeployMode,
    should_set_profile: bool,
    commit: Commit,
) {
    let target_branch_type = target.branch_type(&env.deployer.config);
    env.deployer
        .deploy(&target.branch, target_branch_type, false, None)
        .unwrap();

    let mut expected = vec![
        Call::Hook(HookStatus::Pre, target_branch_type, None, commit),
        Call::Build(env.toplevel_installable()),
    ];
    if should_set_profile {
        expected.push(Call::Profile(env.mock.build_output.borrow().clone()));
    }
    expected.push(Call::Switch(
        env.mock.build_output.borrow().clone(),
        switch_mode,
    ));
    expected.push(Call::Hook(
        HookStatus::Success,
        target_branch_type,
        Some(deploy_mode),
        commit,
    ));

    assert_eq!(env.mock.calls(), expected, "system call sequence mismatch");

    let target_commit = env.deployer.git.get_commit(&target.branch).unwrap();
    assert_eq!(
        env.deployer.git.get_commit(DEPLOYED_BRANCH),
        Some(target_commit)
    );
    assert_eq!(
        env.deployer.git.get_commit(DEPLOYED_BRANCH_SUCCESS),
        Some(target_commit)
    );
    env.mock.clear();
}

#[test]
fn first_deploy_selects_main() {
    let env = TestEnv::new();
    let main = env.commit("main", 100, &[]);

    let target = env.deployer.get_commit_to_deploy().unwrap();
    assert_eq!(target.branch, "origin/main");
    assert!(target.is_new);
    deploy_and_assert(
        &env,
        &target,
        SwitchMode::Switch,
        DeployMode::Switch,
        true,
        main,
    );
    assert_eq!(
        env.deployer.git.get_commit(DEPLOYED_BRANCH_MAIN),
        Some(main)
    );
}

#[test]
fn new_commit_on_main_is_selected() {
    let env = TestEnv::new();
    let main1 = env.commit("main", 100, &[]);

    env.fetch();
    env.set_deployed(main1);
    env.set_deployed_main(main1);

    let main2 = env.commit("main", 200, &[main1]);

    let target = env.deployer.get_commit_to_deploy().unwrap();
    assert_eq!(target.branch, "origin/main");
    assert!(target.is_new);
    deploy_and_assert(
        &env,
        &target,
        SwitchMode::Switch,
        DeployMode::Switch,
        true,
        main2,
    );
    assert_eq!(
        env.deployer.git.get_commit(DEPLOYED_BRANCH_MAIN),
        Some(main2)
    );
}

#[test]
fn new_testing_branch_is_selected() {
    let env = TestEnv::new();
    let main1 = env.commit("main", 100, &[]);

    env.fetch();
    env.set_deployed(main1);
    env.set_deployed_main(main1);

    let testing1 = env.commit("testing/host", 300, &[main1]);

    let target = env.deployer.get_commit_to_deploy().unwrap();
    assert_eq!(target.branch, "origin/testing/host");
    assert!(target.is_new);
    deploy_and_assert(
        &env,
        &target,
        SwitchMode::Test,
        DeployMode::Test,
        false,
        testing1,
    );
    // Deploying a testing branch must not advance the main bookkeeping ref.
    assert_eq!(
        env.deployer.git.get_commit(DEPLOYED_BRANCH_MAIN),
        Some(main1)
    );
}

#[test]
fn new_commit_on_testing_branch_is_selected() {
    let env = TestEnv::new();
    let main1 = env.commit("main", 100, &[]);
    let testing1 = env.commit("testing/host", 300, &[main1]);

    env.fetch();
    env.set_deployed(testing1);
    env.set_deployed_main(main1);

    let testing2 = env.commit("testing/host", 400, &[testing1]);

    let target = env.deployer.get_commit_to_deploy().unwrap();
    assert_eq!(target.branch, "origin/testing/host");
    assert!(target.is_new);
    deploy_and_assert(
        &env,
        &target,
        SwitchMode::Test,
        DeployMode::Test,
        false,
        testing2,
    );
    // The main bookkeeping ref is still untouched by the testing deploy.
    assert_eq!(
        env.deployer.git.get_commit(DEPLOYED_BRANCH_MAIN),
        Some(main1)
    );
}

#[test]
fn merged_testing_branch_falls_back_to_main() {
    let env = TestEnv::new();
    let main1 = env.commit("main", 100, &[]);
    let testing1 = env.commit("testing/host", 300, &[main1]);

    // The testing branch is fast-forward merged into main.
    env.set_branch("main", testing1, true);

    env.fetch();
    env.set_deployed(testing1);
    env.set_deployed_main(main1);

    let target = env.deployer.get_commit_to_deploy().unwrap();
    assert_eq!(target.branch, "origin/main");
    assert!(target.is_new);
    deploy_and_assert(
        &env,
        &target,
        SwitchMode::Switch,
        DeployMode::Switch,
        true,
        testing1,
    );
    assert_eq!(
        env.deployer.git.get_commit(DEPLOYED_BRANCH_MAIN),
        Some(testing1)
    );
}

#[test]
fn already_current_is_not_new() {
    let env = TestEnv::new();
    let main = env.commit("main", 100, &[]);

    env.fetch();
    env.set_deployed(main);
    env.set_deployed_main(main);

    let target = env.deployer.get_commit_to_deploy().unwrap();
    assert_eq!(target.branch, "origin/main");
    assert!(!target.is_new);
}

#[test]
fn newest_testing_branch_wins() {
    let env = TestEnv::new();
    let main = env.commit("main", 100, &[]);
    let _testing_older = env.commit("testing/hostbranch1/host", 500, &[main]);
    let testing_newer = env.commit("testing/hostbranch2/host", 600, &[main]);

    env.fetch();
    env.set_deployed(main);
    env.set_deployed_main(main);

    let target = env.deployer.get_commit_to_deploy().unwrap();
    assert_eq!(target.branch, "origin/testing/hostbranch2/host");
    assert!(target.is_new);
    deploy_and_assert(
        &env,
        &target,
        SwitchMode::Test,
        DeployMode::Test,
        false,
        testing_newer,
    );
}
