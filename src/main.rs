use std::{
    env,
    ffi::CStr,
    fs::exists,
    process::{self, exit},
    rc::Rc,
};

use clap::{ArgAction, Parser, Subcommand};
use nixos_pull_deploy::{
    config::{Config, DEPLOYED_BRANCH, DEPLOYED_BRANCH_SUCCESS, DeployMode},
    deploy::Deployer,
    git::GitWrapper,
    logger::{LogLevel, log},
    real_system::RealSystem,
};

#[derive(Parser)]
struct Cli {
    #[arg(short, long)]
    ask_token: bool,

    #[arg(long)]
    hostname: Option<String>,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    Run {
        #[arg(short, long)]
        rebuild: bool,

        // https://github.com/clap-rs/clap/issues/815
        #[arg(long = "no-magic-rollback", action = ArgAction::SetFalse)]
        magic_rollback: bool,

        #[arg(short, long)]
        deploy_mode_override: Option<DeployMode>,
    },
    Check,
}

fn gethostname() -> Option<String> {
    let mut buffer = [0u8; 64];

    let err = unsafe { libc::gethostname(buffer.as_mut_ptr() as *mut libc::c_char, buffer.len()) };
    if err != 0 {
        return None;
    }

    let c_str = unsafe { CStr::from_ptr(buffer.as_ptr() as *const libc::c_char) };
    c_str.to_str().ok().map(|s| s.to_owned())
}

fn is_rebuilding() -> anyhow::Result<bool> {
    let output = process::Command::new("systemctl")
        .args([
            "is-active",
            "nixos-pull-deploy-switch-to-configuration.service",
        ])
        .output()?;

    Ok(output.status.success())
}

fn print_up_to_date_commit_info(
    target: &nixos_pull_deploy::deploy::DeployTarget,
    git: &GitWrapper,
) {
    assert!(!target.is_new);

    let commit = git.get_commit(&target.branch).unwrap();
    let last_successful_commit = git.get_commit(DEPLOYED_BRANCH_SUCCESS);

    match last_successful_commit {
        Some(last_successful_commit) if last_successful_commit == commit => log(
            &format!("Already on newest {} commit {}:", target.branch, commit),
            LogLevel::Info,
        ),
        _ => log(
            &format!(
                "Previously failed to deploy newest {} commit {}:",
                target.branch, commit
            ),
            LogLevel::Info,
        ),
    }

    log(&git.get_commit_message(commit).unwrap(), LogLevel::Info);
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    let Ok(config_file) = std::env::var("DEPLOY_CONFIG") else {
        log(
            "Environment variable DEPLOY_CONFIG is not set",
            LogLevel::Error,
        );
        exit(1);
    };
    let config = Config::parse(&config_file).expect("Failed to parse config file");

    if unsafe { libc::geteuid() } != 0 {
        log("I can only run as root", LogLevel::Error);
        exit(1);
    }

    let system = Rc::new(RealSystem {});
    let hostname = cli
        .hostname
        .or(gethostname())
        .expect("Failed to get hostname");
    let git = GitWrapper::new(&config.config_dir, &config.origin.url)?;
    let deployer = Deployer::new(config, hostname, git, system);

    match cli.command {
        Commands::Run {
            rebuild,
            magic_rollback,
            deploy_mode_override,
        } => {
            if is_rebuilding()? {
                log("A rebuild is already running", LogLevel::Error);
                exit(1);
            }

            let target = deployer.get_commit_to_deploy()?;
            if !rebuild && !target.is_new {
                print_up_to_date_commit_info(&target, &deployer.git);
                return Ok(());
            }

            deployer.deploy(
                &target.branch,
                target.branch_type(&deployer.config),
                magic_rollback,
                deploy_mode_override,
            )?;
        }
        Commands::Check => {
            if !exists(&deployer.config.config_dir)? {
                log(
                    &format!(
                        "Logcal repo does not exist. Run '{} run' first.",
                        env::current_exe()?
                            .file_name()
                            .ok_or(anyhow::anyhow!("Failed to get executable name"))?
                            .to_str()
                            .ok_or(anyhow::anyhow!("Failed to get executable name"))?
                            .to_owned()
                    ),
                    LogLevel::Warning,
                );
            }

            let target = deployer.get_commit_to_deploy()?;
            if !target.is_new {
                print_up_to_date_commit_info(&target, &deployer.git);
                return Ok(());
            }

            let new_commit_count = deployer.git.get_distance(DEPLOYED_BRANCH, &target.branch)?;
            if new_commit_count > 0 {
                log(
                    &format!(
                        "{} new commit{} available on {}",
                        new_commit_count,
                        if new_commit_count > 1 { "s" } else { "" },
                        &target.branch
                    ),
                    LogLevel::Info,
                );
            } else {
                log(
                    "Newest available commit is older than deployed",
                    LogLevel::Info,
                );
            }
        }
    }
    Ok(())
}
