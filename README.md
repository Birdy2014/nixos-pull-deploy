# nixos-pull-deploy

## Features
- Deploy from git remote
- Automatic rollback if the new configuration can't reach the git remote anymore
- Test changes using (potentially long-lived) host-specific testing branches with support for multiple hosts per testing branch
- Supports with force-pushes to any branch
- Extensible via hooks
- Automatically reboot on kernel/initrd change

## Configuration

Add **nixos-pull-deploy** to your flake inputs:
```nix
nixos-pull-deploy = {
  url = "github:Birdy2014/nixos-pull-deploy";
  inputs.nixpkgs.follows = "nixpkgs";
};
```

and configure it
```nix
{ inputs, ... }:

{
  imports = [ inputs.nixos-pull-deploy.nixosModules.default ];

  services.nixos-pull-deploy = {
    enable = true;
    autoUpgrade = {
      enable = true;
      startAt = "*-*-* 02:00:00";
    };
    settings = {
      origin = {
        url = "https://github.com/...";
        main = "main";
        token_file = config.sops.secrets."deployment-access-token".path;
      };
    };
  };
}
```

A list of all available options can be found in [options.md](./options.md).

## CLI Usage

To just deploy, run:
```bash
nixos-pull-deploy run
```
This command will also initialize the local git repository if it doesn't exist.

To check if a new commit is available without changing anything, run
```bash
nixos-pull-deploy check
```
If the local git repository doesn't exist, this command will fail.

## Design

### Testing Branches

Sometimes, changes to the configuration need to be tested on specific hosts before they are rolled out to all hosts or a team member wants to be able to make quick changes to a branch without others getting in the way.
The solution for this is a testing branch, which only targets specific hosts.

Which hosts are targeted by a testing branch is determined by its name.
With the default prefix `testing/` and separator `/`, the testing branch targeting the hosts `seidenschwanz` and `buntspecht` would be called `testing/seidenschwanz/buntspecht`.
The order of the hostnames does not matter.
If there are multiple matching branches, the branches are checked in descending order of the commit date.
Without any suitable testing branches, the main branch is chosen for deployment.

A testing branch is suitable if the following criteria match:
- The branch is not merged into main
- The tip of the branch is not behind the merge base of the currently deployed commit and the main branch.

The second condition ensures that testing branches will not downgrade the host to an earlier commit and that the host will stay on a testing branch until it is deleted or merged, even after a force-push.

### Deployment Modes

In addition to the modes `test`, `switch` and `boot`, a mode called `reboot` is supported, which makes the new configuration the default and then reboots the host.

The mode to deploy with is configured per branch type (`main` or `testing`) and per situation:
- `normal`: default case
- `kernel_changed`: the kernel, kernel modules or initrd of the new configuration differ from the booted ones
- `inhibited`: the switch inhibitors of the new configuration differ from those of the currently booted configuration

The mode can also be overridden for a single deployment with the flag `--deploy-mode-override` when invoking `nixos-pull-deploy run`.

### Magic Rollback

This feature is inspired by [deploy-rs](https://github.com/serokell/deploy-rs).
It rolls back the configuration automatically when the network connection to the git remote fails after deployment.
Magic rollback is only supported with the deployment modes `test` and `switch`.

Magic rollback is enabled by default, but can be disabled for a single deployment with the flag `--no-magic-rollback` when invoking `nixos-pull-deploy run`.

### Automatic Updates

If `services.nixos-pull-deploy.autoUpgrade` is enabled, a systemd-timer is installed.
