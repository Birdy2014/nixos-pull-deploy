use std::{
    io::{self, IsTerminal},
    os::unix::process::parent_id,
};

pub enum LogLevel {
    Error,
    Warning,
    Info,
}

impl LogLevel {
    fn systemd_priority(self) -> u8 {
        match self {
            LogLevel::Error => 3,
            LogLevel::Warning => 4,
            LogLevel::Info => 6,
        }
    }

    fn color(self) -> &'static str {
        match self {
            LogLevel::Error => "\u{1b}[1;31m",
            LogLevel::Warning => "\u{1b}[0;33m",
            LogLevel::Info => "",
        }
    }
}

pub fn log(message: &str, level: LogLevel) {
    if parent_id() == 1 {
        let priority = level.systemd_priority();
        println!(
            "{}",
            message
                .lines()
                .map(|line| format!("<{priority}>{line}"))
                .collect::<Vec<_>>()
                .join("\n")
        );
        return;
    }

    let stdout = io::stdout();
    if stdout.is_terminal() {
        println!("{}{message}\u{1b}[0m", level.color());
        return;
    }

    println!("{message}");
}
