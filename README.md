# nixos-pull-deploy

## Features
- Deploy from git remote
- Automatic rollback if the new configuration can't reach the git remote anymore
- Test changes using (potentially long-lived) host-specific testing branches
- Supports with force-pushes to any branch
- Extensible via hooks

## Usage

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
        testing = "testing-";
        token_file = config.sops.secrets."deployment-access-token".path;
      };
    };
  };
}
```
