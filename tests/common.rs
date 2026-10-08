use std::fs;
use std::{
    cell::{Cell, RefCell},
    rc::Rc,
};

use nixos_pull_deploy::{
    config::{
        BranchDeployModes, Config, DEPLOYED_BRANCH, DEPLOYED_BRANCH_MAIN, DeployMode, Inhibition,
    },
    deploy::Deployer,
    git::{Commit, GitWrapper},
    system::{BranchType, CommandState, HookStatus, NixError, StorePath, SwitchMode, System},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Call {
    Build(String),
    Switch(StorePath, SwitchMode),
    Profile(StorePath),
    Hook(HookStatus, BranchType, Option<DeployMode>, Commit),
    Reboot,
}

/// The mock's configuration is interior-mutable: the `Deployer` holds the mock
/// as `Rc<dyn System>`, so tests configure it through these setters after the
/// `TestEnv` is built.
pub struct MockSystem {
    pub build_ok: Cell<bool>,
    pub build_cancelled: Cell<bool>,
    pub build_output: RefCell<String>,
    pub switch_ok: Cell<bool>,
    pub inhibition: RefCell<Inhibition>,
    pub current_toplevel: RefCell<String>,
    pub calls: RefCell<Vec<Call>>,
}

impl Default for MockSystem {
    fn default() -> Self {
        Self {
            build_ok: Cell::new(true),
            build_cancelled: Cell::new(false),
            build_output: RefCell::new("/nix/store/mock-toplevel".to_string()),
            switch_ok: Cell::new(true),
            inhibition: RefCell::new(Inhibition::Normal),
            current_toplevel: RefCell::new("/nix/store/current-toplevel".to_string()),
            calls: RefCell::new(Vec::new()),
        }
    }
}

impl MockSystem {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn set_build_ok(&self, ok: bool) {
        self.build_ok.set(ok);
    }

    pub fn set_build_cancelled(&self, cancelled: bool) {
        self.build_cancelled.set(cancelled);
    }

    pub fn set_switch_ok(&self, ok: bool) {
        self.switch_ok.set(ok);
    }

    pub fn set_inhibition(&self, inhibition: Inhibition) {
        *self.inhibition.borrow_mut() = inhibition;
    }

    pub fn calls(&self) -> Vec<Call> {
        self.calls.borrow().clone()
    }

    pub fn record(&self, call: Call) {
        self.calls.borrow_mut().push(call);
    }

    pub fn clear(&self) {
        self.calls.borrow_mut().clear();
    }
}

impl System for MockSystem {
    fn build(&self, installable: &str) -> Result<StorePath, NixError> {
        self.record(Call::Build(installable.to_string()));
        if self.build_cancelled.get() {
            let mut error = NixError {
                state: CommandState::Failed,
                code: 1,
                stderr: "mock build cancelled".to_string(),
                command: vec![],
            };
            error.state = CommandState::Cancelled;
            return Err(error);
        }
        if self.build_ok.get() {
            Ok(self.build_output.borrow().clone())
        } else {
            Err(NixError {
                state: CommandState::Failed,
                code: 1,
                stderr: "mock build failure".to_string(),
                command: vec![],
            })
        }
    }

    fn get_inhibition(&self, _new_config: &StorePath) -> Inhibition {
        *self.inhibition.borrow()
    }

    fn switch_to_configuration(&self, toplevel: &StorePath, mode: SwitchMode) -> bool {
        self.record(Call::Switch(toplevel.to_string(), mode));
        self.switch_ok.get()
    }

    fn set_system_profile(&self, store_path: &StorePath) -> Result<(), NixError> {
        self.record(Call::Profile(store_path.to_string()));
        Ok(())
    }

    fn current_toplevel(&self) -> StorePath {
        self.current_toplevel.borrow().clone()
    }

    fn run_hook(
        &self,
        _deployer: &Deployer,
        status: HookStatus,
        branch_type: &BranchType,
        mode: Option<DeployMode>,
        commit: Commit,
    ) {
        self.record(Call::Hook(status, *branch_type, mode, commit));
    }

    fn reboot(&self) {
        self.record(Call::Reboot);
    }
}

pub fn make_config(local_repo: &str, origin_url: &str) -> Config {
    Config {
        config_dir: local_repo.to_string(),
        origin: nixos_pull_deploy::config::Origin {
            url: origin_url.to_string(),
            main: "main".to_string(),
            testing_prefix: "testing/".to_string(),
            testing_separator: "/".to_string(),
            username: "git".to_owned(),
            token: None,
            token_file: None,
            ssh_key_path: None,
        },
        hook: None,
        deploy_modes: nixos_pull_deploy::config::DeployModes {
            main: BranchDeployModes {
                normal: DeployMode::Switch,
                kernel_changed: DeployMode::Switch,
                inhibited: DeployMode::Switch,
            },
            testing: BranchDeployModes {
                normal: DeployMode::Test,
                kernel_changed: DeployMode::Test,
                inhibited: DeployMode::Switch,
            },
        },
        magic_rollback_timeout: 0,
        fetch_retries: 0,
    }
}

/// Self-contained test environment: a temporary origin and local repo, a
/// `Deployer` wired to a `MockSystem`, and a small imperative API for building
/// the origin's commit graph plus the local `_deployed*` bookkeeping refs.
///
/// The local repo is synced from the origin by the fetch inside
/// `get_commit_to_deploy()`, so tests only build commits on the origin and set
/// the bookkeeping refs on the local repo directly. The tempdir (and both
/// repos) is cleaned up on drop.
pub struct TestEnv {
    _tmp: tempfile::TempDir,
    pub origin: git2::Repository,
    pub deployer: Deployer,
    pub mock: Rc<MockSystem>,
}

impl TestEnv {
    pub fn new() -> Self {
        let tmp = tempfile::TempDir::new().unwrap();
        let origin_path = tmp.path().join("origin");
        let local_path = tmp.path().join("repo");

        fs::create_dir_all(&origin_path).unwrap();
        let origin = git2::Repository::init(&origin_path).unwrap();

        fs::create_dir_all(&local_path).unwrap();
        let origin_url = format!("file://{}", origin_path.display());
        let config = make_config(local_path.to_str().unwrap(), &origin_url);
        let git = GitWrapper::new(
            local_path.to_str().unwrap(),
            &origin_url,
            &config.origin.username,
            config.origin.token.clone(),
            config.origin.ssh_key_path.clone(),
        )
        .unwrap();

        let mock = Rc::new(MockSystem::new());
        let system: Rc<dyn System> = mock.clone();
        let deployer = Deployer::new(config, "host".to_string(), git, system);

        Self {
            _tmp: tmp,
            origin,
            deployer,
            mock,
        }
    }

    /// Create a commit on the origin repository, returning its id.
    pub fn commit(&self, branch: &str, timestamp: i64, parents: &[git2::Oid]) -> git2::Oid {
        let signature =
            git2::Signature::new("Test", "test@example.com", &git2::Time::new(timestamp, 0))
                .unwrap();
        let tree_id = self.origin.treebuilder(None).unwrap().write().unwrap();
        let tree = self.origin.find_tree(tree_id).unwrap();
        let parent_commits: Vec<git2::Commit> = parents
            .iter()
            .map(|oid| self.origin.find_commit(*oid).unwrap())
            .collect();
        let parents: Vec<&git2::Commit> = parent_commits.iter().collect();
        self.origin
            .commit(
                Some(&format!("refs/heads/{}", branch)),
                &signature,
                &signature,
                "test commit",
                &tree,
                &parents,
            )
            .unwrap()
    }

    pub fn set_branch(&self, branch: &str, target: git2::Oid, force: bool) {
        self.origin
            .reference(
                &format!("refs/heads/{}", branch),
                target,
                force,
                "test: set branch",
            )
            .unwrap();
    }

    /// Delete a local branch on the origin repository.
    pub fn delete_branch(&self, branch: &str) {
        self.origin
            .find_branch(branch, git2::BranchType::Local)
            .unwrap()
            .delete()
            .unwrap();
    }

    /// Sync the local repo from the origin.
    pub fn fetch(&self) {
        self.deployer.git.fetch(0).unwrap();
    }

    /// Make the origin unreachable: subsequent `fetch`es on the local repo
    /// fail. Used to exercise the magic-rollback path.
    pub fn remove_origin(&self) {
        fs::remove_dir_all(self.origin.path()).unwrap();
    }

    pub fn set_deployed(&self, target: git2::Oid) {
        self.deployer
            .git
            .set_branch_to(DEPLOYED_BRANCH, target)
            .unwrap();
    }

    pub fn set_deployed_main(&self, target: git2::Oid) {
        self.deployer
            .git
            .set_branch_to(DEPLOYED_BRANCH_MAIN, target)
            .unwrap();
    }

    pub fn toplevel_installable(&self) -> String {
        format!(
            "{}#nixosConfigurations.\"host\".config.system.build.toplevel",
            self.deployer.git.path()
        )
    }
}
