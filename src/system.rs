use std::{error::Error, fmt::Display};

use crate::{config::DeployMode, deploy::Deployer, git::Commit};

pub type StorePath = String;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Inhibition {
    Normal,
    KernelChanged,
    Inhibited,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SwitchMode {
    Test,
    Switch,
    Boot,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HookStatus {
    Pre,
    Success,
    Failed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BranchType {
    Main,
    Testing,
}

#[derive(Debug, PartialEq)]
pub enum CommandState {
    Failed,
    Cancelled,
    NoOutput,
}

#[derive(Debug)]
pub struct NixError {
    pub state: CommandState,
    pub code: i32,
    pub stderr: String,
    pub command: Vec<String>,
}

impl Display for NixError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let command = self.command.join(" ");
        match self.state {
            CommandState::Failed => write!(
                f,
                "Nix failed with code {}\ncommand: {}",
                self.code, command
            ),
            CommandState::Cancelled => write!(f, "Nix command was cancelled\ncommand: {}", command),
            CommandState::NoOutput => write!(f, "Nix produced no output\ncommand: {}", command),
        }
    }
}

impl Error for NixError {}

pub trait System {
    fn build(&self, installable: &str) -> Result<StorePath, NixError>;
    fn get_inhibition(&self, new_config: &StorePath) -> Inhibition;
    fn switch_to_configuration(&self, toplevel: &StorePath, mode: SwitchMode) -> bool;
    fn set_system_profile(&self, store_path: &StorePath) -> Result<(), NixError>;
    fn current_toplevel(&self) -> StorePath;
    fn run_hook(
        &self,
        hook_path: &Deployer,
        status: HookStatus,
        branch_type: &BranchType,
        mode: Option<DeployMode>,
        commit: Commit,
    );
    fn reboot(&self);
}
