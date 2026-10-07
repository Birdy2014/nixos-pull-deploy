use clap::ValueEnum;
use serde::Deserialize;
use std::fs::read_to_string;

use crate::system::Inhibition;

pub const DEPLOYED_BRANCH: &str = "_deployed";
pub const DEPLOYED_BRANCH_MAIN: &str = "_deployed_main";
pub const DEPLOYED_BRANCH_SUCCESS: &str = "_deployed_success";

#[derive(Deserialize)]
pub struct Config {
    pub config_dir: String,
    pub origin: Origin,
    pub hook: Option<String>,
    pub deploy_modes: DeployModes,
    pub magic_rollback_timeout: usize,
    pub fetch_retries: usize,
}

#[derive(Deserialize)]
pub struct Origin {
    pub url: String,
    pub main: String,
    pub testing_prefix: String,
    pub testing_separator: String,
    pub username: String,
    pub token: Option<String>,
    pub token_file: Option<String>,
    pub ssh_key_path: Option<String>,
}

#[derive(Deserialize)]
pub struct DeployModes {
    pub main: BranchDeployModes,
    pub testing: BranchDeployModes,
}

#[derive(Debug, Deserialize, Clone, Copy, PartialEq, Eq, ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum DeployMode {
    Test,
    Switch,
    Boot,
    Reboot,
}

#[derive(Deserialize)]
pub struct BranchDeployModes {
    pub normal: DeployMode,
    pub kernel_changed: DeployMode,
    pub inhibited: DeployMode,
}

impl BranchDeployModes {
    pub fn get(&self, inhibition: &crate::system::Inhibition) -> DeployMode {
        match inhibition {
            Inhibition::Normal => self.normal,
            Inhibition::KernelChanged => self.kernel_changed,
            Inhibition::Inhibited => self.inhibited,
        }
    }
}

impl Config {
    pub fn parse(path: &str) -> anyhow::Result<Config> {
        let file_content = read_to_string(path)?;
        Ok(toml::from_str(&file_content)?)
    }
}
