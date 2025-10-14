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

The following modes work the same as in `nixos-rebuild`:
- `test`
- `switch`
- `boot`

These modes are custom:
- `reboot`
  - Sets the new generation as default (like `boot`) and reboots the host.
- `reboot_on_kernel_change`
  - Behaves like `reboot` if the kernel or initrd changed, otherwise it will `switch`.

### Magic Rollback

This feature is inspired by [deploy-rs](https://github.com/serokell/deploy-rs).
It is meant to rollback automatically when a change breaks the network connection.
To do this, the connection to the git remote is checked after a deployment using either of the modes `test`, `switch`, `reboot_on_kernel_change` (only when the kernel/initrd didn't change).
If the connection fails, the host is reverted to the previously deployed configuration.

Magic rollback can be temporarily disabled with the flag `--no-magic-rollback` when invoking `nixos-pull-deploy run`.

### Automatic Updates

If `services.nixos-pull-deploy.autoUpgrade` is enabled, a systemd-timer is installed.
