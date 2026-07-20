import enum
import os
import select
import signal
import subprocess
import sys
from .logger import *


class CommandState(enum.Enum):
    FAILED = 0
    CANCELLED = 1
    NO_OUTPUT = 2


class NixException(Exception):
    state: CommandState
    code: int
    stderr: str
    command: list[str]

    def __init__(
        self, state: CommandState, code: int, stderr: str, command: list[str]
    ) -> None:
        self.state = state
        self.code = code
        self.stderr = stderr
        self.command = command
        match state:
            case CommandState.FAILED:
                super().__init__(
                    f"Nix failed with code {code}\ncommand: {" ".join(command)}"
                )
            case CommandState.CANCELLED:
                super().__init__(
                    f"Nix command was cancelled\ncommand: {" ".join(command)}"
                )
            case CommandState.NO_OUTPUT:
                super().__init__(
                    f"Nix produced no output\ncommand: {" ".join(command)}"
                )


def communicate_print(
    process: subprocess.Popen[bytes], print_stdout: bool
) -> tuple[str, str]:
    assert process.stdout is not None
    assert process.stderr is not None

    stdout = ""
    stderr = ""

    dataend = False
    while (process.returncode is None) or (not dataend):
        process.poll()
        ready = select.select([process.stdout, process.stderr], [], [])
        if process.stderr in ready[0]:
            data = os.read(process.stderr.fileno(), 1024).decode("utf-8")
            stderr += data
            sys.stderr.write(data)
        if process.stdout in ready[0]:
            data = os.read(process.stdout.fileno(), 1024).decode("utf-8")
            if len(data) > 0:
                stdout += data
                if print_stdout:
                    sys.stdout.write(data)
            else:
                dataend = True

    return stdout, stderr


def run_nix_cancelable(command: list[str], print_stdout: bool = True) -> str:
    command = ["nix", "--extra-experimental-features", "nix-command flakes", *command]
    cancelled = False

    original_handler_sigint = signal.getsignal(signal.SIGINT)
    original_handler_sigterm = signal.getsignal(signal.SIGTERM)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=None,
        start_new_session=True,
    )

    assert process.stdout is not None
    assert process.stderr is not None

    def handler(signum, _):
        nonlocal cancelled
        cancelled = True
        process.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    stdout, stderr = communicate_print(process, print_stdout)

    signal.signal(signal.SIGINT, original_handler_sigint)
    signal.signal(signal.SIGTERM, original_handler_sigterm)

    if cancelled:
        state = CommandState.CANCELLED
    elif process.returncode != 0:
        state = CommandState.FAILED
        log(
            f"Error: nix command exited with code {process.returncode}",
            LogLevel.ERROR,
        )
    else:
        return stdout

    raise NixException(
        state=state, code=process.returncode, stderr=stderr, command=command
    )


def nix_build(derivation: str) -> str:
    command = [
        "build",
        "--no-link",
        "--print-out-paths",
        derivation,
    ]

    result = run_nix_cancelable(command)

    log(f"Build output: {result}")

    path = result.strip()
    if path.startswith("/nix/store"):
        return path

    log("Error: nix build produced no output", LogLevel.ERROR)
    raise NixException(CommandState.NO_OUTPUT, 0, "", command)


def nix_set_system_profile(store_path: str):
    profile = "/nix/var/nix/profiles/system"
    command = ["nix-env", "-p", profile, "--set", store_path]
    process = subprocess.run(command, capture_output=True)
    if process.returncode != 0:
        raise NixException(
            CommandState.FAILED,
            process.returncode,
            process.stderr.decode("utf-8"),
            command,
        )
