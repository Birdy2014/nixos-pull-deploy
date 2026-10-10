use std::{
    fs::{read_link, read_to_string},
    io::Write,
    os::unix::{
        io::AsRawFd,
        process::{CommandExt, parent_id},
    },
    process::{Command, Stdio},
    sync::atomic::{AtomicBool, AtomicI32, Ordering},
};

use crate::{
    config::{DEPLOYED_BRANCH_SUCCESS, DeployMode, Inhibition},
    deploy::Deployer,
    git::Commit,
    logger::{LogLevel, log},
    system::{BranchType, CommandState, HookStatus, NixError, StorePath, SwitchMode, System},
};

static CANCELLED: AtomicBool = AtomicBool::new(false);
static CHILD_PID: AtomicI32 = AtomicI32::new(0);

extern "C" fn on_signal(_sig: libc::c_int) {
    CANCELLED.store(true, Ordering::SeqCst);
    let pid = CHILD_PID.load(Ordering::SeqCst);
    if pid > 0 {
        unsafe {
            libc::kill(-(pid as libc::pid_t), libc::SIGTERM);
        }
    }
}

/// Run a `nix` subcommand in its own session, forwarding SIGINT/SIGTERM to the
/// child so a `Ctrl-C` cancels the whole build. Returns the captured stdout.
fn run_nix_cancelable(command: &[String], print_stdout: bool) -> Result<String, NixError> {
    let mut full_command: Vec<String> = vec![
        "nix".to_string(),
        "--extra-experimental-features".to_string(),
        "nix-command flakes".to_string(),
    ];
    full_command.extend_from_slice(command);

    let mut nix_command = Command::new(&full_command[0]);
    nix_command
        .args(&full_command[1..])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // Start the child in its own session so it leads a new process group,
    // letting us signal the whole tree
    unsafe {
        nix_command.pre_exec(|| {
            if libc::setsid() == -1 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = nix_command.spawn().map_err(|error| NixError {
        state: CommandState::Failed,
        code: -1,
        stderr: error.to_string(),
        command: full_command.clone(),
    })?;

    CANCELLED.store(false, Ordering::SeqCst);
    CHILD_PID.store(child.id() as i32, Ordering::SeqCst);

    let stdout = child.stdout.take().expect("stdout is piped");
    let stderr = child.stderr.take().expect("stderr is piped");

    // Install handlers that forward SIGTERM to the child's process group.
    let mut new_action: libc::sigaction = unsafe { std::mem::zeroed() };
    new_action.sa_sigaction = on_signal as *const () as usize;
    let mut old_int: libc::sigaction = unsafe { std::mem::zeroed() };
    let mut old_term: libc::sigaction = unsafe { std::mem::zeroed() };
    unsafe {
        libc::sigaction(libc::SIGINT, &new_action, &mut old_int);
        libc::sigaction(libc::SIGTERM, &new_action, &mut old_term);
    }

    // Read stdout and stderr concurrently with `poll`, printing each stream to
    // the terminal in real time as it arrives.
    let out_fd = stdout.as_raw_fd();
    let err_fd = stderr.as_raw_fd();
    let mut out_text = String::new();
    let mut err_text = String::new();
    let mut read_buf = [0u8; 1024];
    let mut fds = [
        libc::pollfd {
            fd: out_fd,
            events: libc::POLLIN,
            revents: 0,
        },
        libc::pollfd {
            fd: err_fd,
            events: libc::POLLIN,
            revents: 0,
        },
    ];

    while fds[0].fd != -1 || fds[1].fd != -1 {
        let _ = unsafe { libc::poll(fds.as_mut_ptr(), 2, -1) };

        if fds[0].fd != -1 && fds[0].revents != 0 {
            let n = unsafe { libc::read(out_fd, read_buf.as_mut_ptr() as *mut _, read_buf.len()) };
            if n <= 0 {
                fds[0].fd = -1;
            } else {
                let chunk = &read_buf[..n as usize];
                out_text.push_str(&String::from_utf8_lossy(chunk));
                if print_stdout {
                    let _ = std::io::stdout().write_all(chunk);
                    let _ = std::io::stdout().flush();
                }
            }
        }
        if fds[1].fd != -1 && fds[1].revents != 0 {
            let n = unsafe { libc::read(err_fd, read_buf.as_mut_ptr() as *mut _, read_buf.len()) };
            if n <= 0 {
                fds[1].fd = -1;
            } else {
                let chunk = &read_buf[..n as usize];
                err_text.push_str(&String::from_utf8_lossy(chunk));
                let _ = std::io::stderr().write_all(chunk);
                let _ = std::io::stderr().flush();
            }
        }
    }

    let status = child.wait().expect("failed to wait for nix child process");

    // Restore the original signal handlers and clear the child pid.
    unsafe {
        libc::sigaction(libc::SIGINT, &old_int, std::ptr::null_mut());
        libc::sigaction(libc::SIGTERM, &old_term, std::ptr::null_mut());
    }
    CHILD_PID.store(0, Ordering::SeqCst);

    let code = status.code().unwrap_or(0);
    if CANCELLED.load(Ordering::SeqCst) {
        return Err(NixError {
            state: CommandState::Cancelled,
            code,
            stderr: err_text,
            command: full_command,
        });
    }
    if !status.success() {
        log(
            &format!("Error: nix command exited with code {code}"),
            LogLevel::Error,
        );
        return Err(NixError {
            state: CommandState::Failed,
            code,
            stderr: err_text,
            command: full_command,
        });
    }
    Ok(out_text)
}

pub struct RealSystem {}

impl System for RealSystem {
    fn build(&self, installable: &str) -> Result<StorePath, NixError> {
        let command = vec![
            "build".to_string(),
            "--no-link".to_string(),
            "--print-out-paths".to_string(),
            installable.to_string(),
        ];

        let result = run_nix_cancelable(&command, false)?;

        log(&format!("Build output: {result}"), LogLevel::Info);

        let path = result.trim();
        if path.starts_with("/nix/store") {
            return Ok(path.to_string());
        }

        log("Error: nix build produced no output", LogLevel::Error);
        Err(NixError {
            state: CommandState::NoOutput,
            code: 0,
            stderr: String::new(),
            command,
        })
    }

    fn get_inhibition(&self, new_config: &StorePath) -> Inhibition {
        if read_to_string("/run/booted-system/switch-inhibitors").unwrap()
            != read_to_string(format!("{}/switch-inhibitors", new_config)).unwrap()
        {
            return Inhibition::Inhibited;
        }

        if ["kernel", "kernel-modules", "initrd"]
            .into_iter()
            .any(|name| {
                read_link(format!("/run/booted-system/{}", name)).unwrap()
                    != read_link(format!("{}/{}", new_config, name)).unwrap()
            })
        {
            return Inhibition::KernelChanged;
        }

        Inhibition::Normal
    }

    fn switch_to_configuration(&self, toplevel: &StorePath, mode: SwitchMode) -> bool {
        let mode_string = match mode {
            SwitchMode::Test => "test",
            SwitchMode::Switch => "switch",
            SwitchMode::Boot => "boot",
        };

        let command = vec![
            "systemd-run".to_owned(),
            "-E".to_owned(),
            "LOCALE_ARCHIVE".to_owned(), // Will be set to new value early in switch-to-configuration script, but interpreter starts out with old value
            "-E".to_owned(),
            "NIXOS_INSTALL_BOOTLOADER=0".to_owned(),
            "-E".to_owned(),
            "NIXOS_NO_CHECK=1".to_owned(),
            "--collect".to_owned(),
            "--no-ask-password".to_owned(),
            "--pipe".to_owned(),
            "--quiet".to_owned(),
            "--service-type=exec".to_owned(),
            "--unit=nixos-pull-deploy-switch-to-configuration".to_owned(),
            "--wait".to_owned(),
            format!("{}/bin/switch-to-configuration", toplevel),
            mode_string.to_owned(),
        ];

        let Ok(mut child) = Command::new(&command[0]).args(&command[1..]).spawn() else {
            return false;
        };

        let Ok(status) = child.wait() else {
            return false;
        };

        status.success()
    }

    fn set_system_profile(&self, store_path: &StorePath) -> Result<(), NixError> {
        let command = vec![
            "nix-env".to_string(),
            "-p".to_string(),
            "/nix/var/nix/profiles/system".to_string(),
            "--set".to_string(),
            store_path.to_string(),
        ];

        let output = Command::new(&command[0])
            .args(&command[1..])
            .output()
            .map_err(|error| NixError {
                state: CommandState::Failed,
                code: -1,
                stderr: error.to_string(),
                command: command.clone(),
            })?;

        if !output.status.success() {
            return Err(NixError {
                state: CommandState::Failed,
                code: output.status.code().unwrap_or(0),
                stderr: String::from_utf8_lossy(&output.stderr).to_string(),
                command,
            });
        }
        Ok(())
    }

    fn current_toplevel(&self) -> StorePath {
        read_link("/run/current-system")
            .unwrap()
            .to_str()
            .unwrap()
            .to_owned()
    }

    fn run_hook(
        &self,
        deployer: &Deployer,
        status: HookStatus,
        branch_type: &BranchType,
        mode: Option<DeployMode>,
        commit: Commit,
    ) {
        let Some(hook_path) = &deployer.config.hook else {
            return;
        };

        let deploy_success_commit = deployer.git.get_commit(DEPLOYED_BRANCH_SUCCESS);

        let status = Command::new(hook_path)
            .env(
                "DEPLOY_STATUS",
                match status {
                    HookStatus::Pre => "pre",
                    HookStatus::Success => "success",
                    HookStatus::Failed => "failed",
                },
            )
            .env(
                "DEPLOY_TYPE",
                match branch_type {
                    BranchType::Main => "main",
                    BranchType::Testing => "testing",
                },
            )
            .env(
                "DEPLOY_MODE",
                match mode {
                    Some(mode) => match mode {
                        DeployMode::Test => "test",
                        DeployMode::Switch => "switch",
                        DeployMode::Boot => "boot",
                        DeployMode::Reboot => "reboot",
                    },
                    None => "",
                },
            )
            .env("DEPLOY_COMMIT", commit.to_string())
            .env(
                "DEPLOY_COMMIT_MESSAGE",
                deployer.git.get_commit_message(commit).unwrap(),
            )
            .env(
                "DEPLOY_SUCCESS_COMMIT",
                deploy_success_commit
                    .map(|oid| oid.to_string())
                    .unwrap_or_default(),
            )
            .env(
                "DEPLOY_SUCCESS_COMMIT_MESSAGE",
                deploy_success_commit
                    .map(|commit| deployer.git.get_commit_message(commit).unwrap())
                    .unwrap_or_default(),
            )
            .env("DEPLOY_SCHEDULED", if parent_id() == 1 { "1" } else { "0" })
            .status();

        match status {
            Ok(status) if status.code().is_some() => {
                if !status.success() {
                    log(
                        &format!("hook exited with code {}", status.code().unwrap()),
                        LogLevel::Error,
                    )
                }
            }
            _ => log("hook failed", LogLevel::Error),
        };
    }

    fn reboot(&self) {
        let output = Command::new("systemctl")
            .args(["reboot", "--when=+1min"])
            .output()
            .unwrap();

        assert!(output.status.success());
    }
}
