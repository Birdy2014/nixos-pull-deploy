import dataclasses
import enum
import json
import os
import re
import select
import shlex
import signal
import subprocess
import sys
from .logger import *


class CommandState(enum.Enum):
    FAILED = 0
    CANCELLED = 1
    CONNECTION_FAILED = 2
    NO_OUTPUT = 3


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
            case CommandState.CONNECTION_FAILED:
                super().__init__(
                    f"Connection to remote host failed\ncommand: {" ".join(command)}"
                )
            case CommandState.NO_OUTPUT:
                super().__init__(
                    f"Nix produced no output\ncommand: {" ".join(command)}"
                )


@dataclasses.dataclass
class Remote:
    host: str
    port: int

    @classmethod
    def parse(cls, text: str) -> "Remote | None":
        p = re.compile(
            "^(([a-z]+@([a-zA-Z0-9.\\-]+|(\\[[a-zA-Z0-9:]+\\])))(:([0-9]+))?)$", re.M
        )
        match = p.match(text)
        if match is None:
            log(f"Failed to parse host {text}", LogLevel.ERROR)
            return None

        return cls(host=match.group(2), port=int(match.group(6) or 22))


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


def run_nix_cancelable(
    command: list[str], remote: Remote | None = None, print_stdout: bool = True
) -> str:
    command = ["nix", "--extra-experimental-features", "nix-command flakes", *command]

    if remote is not None:
        command = [
            "ssh",
            "-o",
            "ConnectTimeout=3",
            remote.host,
            "-p",
            str(remote.port),
            "--",
        ] + list(map(shlex.quote, command))

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
    elif remote is not None and process.returncode == 255:
        state = CommandState.CONNECTION_FAILED
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


def nix_build(derivation: str, remote: Remote | None) -> str:
    command = [
        "build",
        "--no-link",
        "--print-out-paths",
        derivation,
    ]

    result = run_nix_cancelable(command, remote)

    log(f"Build output: {result}")

    path = result.strip()
    if path.startswith("/nix/store"):
        return path

    log("Error: nix build produced no output", LogLevel.ERROR)
    raise NixException(CommandState.NO_OUTPUT, 0, "", command)


def nix_copy(derivation: str, from_host: Remote | None, to_host: Remote | None):
    if from_host is None and to_host is None:
        return

    command = ["copy", "--no-check-sigs"]
    if from_host is not None:
        command += ["--from", f"ssh://{from_host.host}:{from_host.port}"]
    if to_host is not None:
        command += ["--to", f"ssh://{to_host.host}:{to_host.port}"]
    command += [derivation]

    try:
        run_nix_cancelable(command)
    except NixException as exception:
        if exception.code == 1 and "failed to start SSH connection" in exception.stderr:
            raise NixException(
                CommandState.CONNECTION_FAILED,
                exception.code,
                exception.stderr,
                exception.command,
            )
        raise exception


def nix_archive(flake: str) -> str:
    output = run_nix_cancelable(
        ["flake", "archive", "--json", "--no-update-lock-file", flake], None, False
    )
    parsed = json.loads(output)
    return parsed["path"]


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
