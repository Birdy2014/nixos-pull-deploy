import os
import sys
import enum


class LogLevel(enum.Enum):
    ERROR = 1
    WARNING = 2
    INFO = 3


systemd_priority = {
    LogLevel.ERROR: 3,
    LogLevel.WARNING: 4,
    LogLevel.INFO: 6,
}

colors = {
    LogLevel.ERROR: "\033[1;31m",
    LogLevel.WARNING: "\033[0;33m",
    LogLevel.INFO: "",
}


def log(message: str, level: LogLevel = LogLevel.INFO) -> None:
    if os.getppid() == 1:
        print(
            "\n".join(
                map(
                    lambda line: f"<{systemd_priority[level]}>{line}",
                    message.split("\n"),
                )
            )
        )
        return

    if sys.stdout.isatty():
        print(f"{colors[level]}{message}\033[0m")
        return

    print(message)
