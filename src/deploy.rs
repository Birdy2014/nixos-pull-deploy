use std::{
    error, fmt,
    io::{Write, stdout},
    process::exit,
    rc::Rc,
};

use crate::{
    config::{
        Config, DEPLOYED_BRANCH, DEPLOYED_BRANCH_MAIN, DEPLOYED_BRANCH_SUCCESS, DeployMode,
        Inhibition,
    },
    git::GitWrapper,
    logger::{LogLevel, log},
    system::{BranchType, CommandState, HookStatus, NixError, SwitchMode, System},
};

pub struct Deployer {
    pub config: Config,
    pub hostname: String,
    pub git: GitWrapper,
    pub system: Rc<dyn System>,
}

pub struct DeployTarget {
    pub branch: String,
    pub is_new: bool,
}

impl DeployTarget {
    pub fn branch_type(&self, config: &Config) -> BranchType {
        let remote = "origin";
        if self.branch == format!("{}/{}", remote, config.origin.main) {
            BranchType::Main
        } else if self
            .branch
            .starts_with(&format!("{}/{}", remote, config.origin.testing_prefix))
        {
            BranchType::Testing
        } else {
            panic!("Invalid branch type")
        }
    }
}

impl Deployer {
    pub fn new(config: Config, hostname: String, git: GitWrapper, system: Rc<dyn System>) -> Self {
        Self {
            config,
            hostname,
            git,
            system,
        }
    }

    pub fn get_commit_to_deploy(&self) -> Result<DeployTarget, git2::Error> {
        let main_branch = format!("origin/{}", self.config.origin.main);

        self.git.fetch(self.config.fetch_retries)?;

        let testing_branches = self
            .git
            .list_remote_branches()?
            .into_iter()
            .filter(|branch| {
                let prefix = format!("origin/{}", self.config.origin.testing_prefix);
                if !branch.starts_with(&prefix) {
                    return false;
                }
                branch[prefix.len()..branch.len()]
                    .split(&self.config.origin.testing_separator)
                    .any(|v| v == self.hostname)
            })
            .collect::<Vec<_>>();

        if testing_branches.len() > 1 {
            log(
                &format!(
                    "Found {} testing branches targeting this host:\n{}",
                    testing_branches.len(),
                    testing_branches
                        .iter()
                        .map(|branch| format!("- {}", branch))
                        .collect::<Vec<_>>()
                        .join("\n")
                ),
                LogLevel::Warning,
            );
        }

        let Some(main_commit) = self.git.get_commit(&main_branch) else {
            log(
                &format!("main branch '{}' does not exist", main_branch),
                LogLevel::Error,
            );
            exit(1);
        };

        let deployed_commit = self.git.get_commit(DEPLOYED_BRANCH);

        for testing_branch in testing_branches {
            let Some(testing_commit) = self.git.get_commit(&testing_branch) else {
                continue;
            };

            match deployed_commit {
                Some(deployed_commit) => {
                    let main_base_commit = self.git.get_base(deployed_commit, main_commit)?;

                    // already on the testing branch or on a former testing branch (after force-push)
                    // and testing branch is not merged into main branch
                    if self.git.is_ancestor(main_base_commit, testing_commit)?
                        && !self.git.is_ancestor(testing_commit, main_commit)?
                    {
                        return Ok(DeployTarget {
                            branch: testing_branch,
                            is_new: deployed_commit != testing_commit,
                        });
                    }
                }
                None => {
                    // testing branch is not merged into main branch
                    if !self.git.is_ancestor(testing_commit, main_commit)? {
                        self.git.set_branch_to(DEPLOYED_BRANCH, testing_commit)?;
                        return Ok(DeployTarget {
                            branch: testing_branch,
                            is_new: true,
                        });
                    }
                }
            }
        }

        match deployed_commit {
            Some(deployed_commit) => {
                let deployed_main_commit = self.git.get_commit(DEPLOYED_BRANCH_MAIN);
                Ok(DeployTarget {
                    branch: main_branch,
                    is_new: deployed_commit != main_commit
                        || deployed_main_commit.is_some_and(|deployed_main_commit| {
                            deployed_main_commit != main_commit
                        }),
                })
            }
            None => {
                self.git.set_branch_to(DEPLOYED_BRANCH, main_commit)?;
                Ok(DeployTarget {
                    branch: main_branch,
                    is_new: true,
                })
            }
        }
    }

    pub fn deploy(
        &self,
        branch: &str,
        branch_type: BranchType,
        magic_rollback: bool,
        deploy_mode_override: Option<DeployMode>,
    ) -> Result<(), DeployError> {
        let Some(commit) = self.git.get_commit(branch) else {
            log(&format!("No commit on branch {}", branch), LogLevel::Error);
            exit(1);
        };

        self.git.checkout(commit)?;

        log(
            &format!("Building {} on branch {}", commit, branch),
            LogLevel::Info,
        );

        log(&self.git.get_commit_message(commit)?, LogLevel::Info);
        log("", LogLevel::Info);
        stdout().flush().unwrap();

        self.system
            .run_hook(self, HookStatus::Pre, &branch_type, None, commit);

        let toplevel_installable = format!(
            "{}#nixosConfigurations.\"{}\".config.system.build.toplevel",
            self.config.config_dir, self.hostname
        );
        let build_output = self.system.build(&toplevel_installable);
        if build_output
            .as_ref()
            .is_err_and(|err| err.state == CommandState::Cancelled)
        {
            log("nix build was cancelled", LogLevel::Warning);
            // do not run hook
            // do not set DEPLOYED_BRANCH: deployment can be retried
            return Err(build_output.unwrap_err().into());
        }

        // set deployed branch early to prevent rebuilding a broken configuration
        self.git.set_branch_to(DEPLOYED_BRANCH, commit)?;
        if branch_type == BranchType::Main {
            self.git.set_branch_to(DEPLOYED_BRANCH_MAIN, commit)?;
        }

        let Ok(build_output) = build_output else {
            log("Build failed", LogLevel::Error);
            self.system
                .run_hook(self, HookStatus::Failed, &branch_type, None, commit);
            return Err(build_output.unwrap_err().into());
        };

        let inhibition = self.system.get_inhibition(&build_output);
        let mode = match deploy_mode_override {
            Some(deploy_mode_override) => deploy_mode_override,
            None => self.get_deploy_mode(&branch_type, &inhibition),
        };

        log(
            &format!(
                "Deploying with mode {:?}, inhibition {:?}\n",
                mode, inhibition
            ),
            LogLevel::Info,
        );
        stdout().flush().unwrap();

        let old_toplevel = self.system.current_toplevel();

        let magic_rollback = match mode {
            DeployMode::Boot => false,
            DeployMode::Reboot => false,
            _ => magic_rollback,
        };

        if magic_rollback {
            if !self
                .system
                .switch_to_configuration(&build_output, SwitchMode::Test)
            {
                log("switch-to-configuration failed", LogLevel::Error);
                self.system
                    .run_hook(self, HookStatus::Failed, &branch_type, Some(mode), commit);
                return Err(DeployError::Switch);
            }

            if self.git.fetch(self.config.magic_rollback_timeout).is_err() {
                log("No network connection - rolling back", LogLevel::Error);
                if !self
                    .system
                    .switch_to_configuration(&old_toplevel, SwitchMode::Test)
                {
                    log("switch-to-configuration failed", LogLevel::Error);
                    self.system.run_hook(
                        self,
                        HookStatus::Failed,
                        &branch_type,
                        Some(mode),
                        commit,
                    );
                    return Err(DeployError::Switch);
                }

                log(
                    "\nRolled back to previous generation because the network connection check failed",
                    LogLevel::Error,
                );
                self.system
                    .run_hook(self, HookStatus::Failed, &branch_type, Some(mode), commit);
                return Err(DeployError::Rollback);
            }
        }

        let switch_mode = match (mode, magic_rollback) {
            (DeployMode::Test, true) => None,
            (DeployMode::Test, false) => Some(SwitchMode::Test),
            (DeployMode::Switch, true) => Some(SwitchMode::Boot),
            (DeployMode::Switch, false) => Some(SwitchMode::Switch),
            _ => Some(SwitchMode::Boot),
        };

        if mode != DeployMode::Test {
            self.system.set_system_profile(&build_output)?;
        }

        if switch_mode.is_some_and(|switch_mode| {
            !self
                .system
                .switch_to_configuration(&build_output, switch_mode)
        }) {
            log("switch-to-configuration failed", LogLevel::Error);
            self.system
                .run_hook(self, HookStatus::Failed, &branch_type, Some(mode), commit);
            return Err(DeployError::Switch);
        }

        self.git.set_branch_to(DEPLOYED_BRANCH_SUCCESS, commit)?;

        log(
            &format!("\nDeployment succeeded: {:?}", mode),
            LogLevel::Info,
        );
        self.system
            .run_hook(self, HookStatus::Success, &branch_type, Some(mode), commit);

        if mode == DeployMode::Reboot {
            log("Rebooting in 1 minute", LogLevel::Info);
            self.system.reboot();
        }

        Ok(())
    }

    fn get_deploy_mode(&self, branch_type: &BranchType, inhibition: &Inhibition) -> DeployMode {
        match branch_type {
            BranchType::Main => self.config.deploy_modes.main.get(inhibition),
            BranchType::Testing => self.config.deploy_modes.testing.get(inhibition),
        }
    }
}

#[derive(Debug)]
pub enum DeployError {
    Git(git2::Error),
    Nix(NixError),
    Switch,
    Rollback,
}

impl fmt::Display for DeployError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match *self {
            DeployError::Git(_) => write!(f, "git call failed"),
            DeployError::Nix(_) => write!(f, "nix call failed"),
            DeployError::Switch => write!(f, "switch-to-configuration failed"),
            DeployError::Rollback => write!(f, "rolled back to previous generation"),
        }
    }
}

impl error::Error for DeployError {
    fn source(&self) -> Option<&(dyn error::Error + 'static)> {
        match *self {
            DeployError::Git(ref error) => Some(error),
            DeployError::Nix(ref error) => Some(error),
            DeployError::Switch => None,
            DeployError::Rollback => None,
        }
    }
}

impl From<git2::Error> for DeployError {
    fn from(value: git2::Error) -> Self {
        Self::Git(value)
    }
}

impl From<NixError> for DeployError {
    fn from(value: NixError) -> Self {
        Self::Nix(value)
    }
}
