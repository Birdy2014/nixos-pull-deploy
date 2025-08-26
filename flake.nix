{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];

      forAllSupportedSystems =
        f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      formatter = forAllSupportedSystems (pkgs: pkgs.nixfmt-tree);

      nixosModules.default =
        {
          config,
          lib,
          pkgs,
          ...
        }:
        let
          cfg = config.services.nixos-pull-deploy;

          removeNull =
            input:
            lib.mapAttrs (name: value: if lib.isAttrs value then removeNull value else value) (
              lib.filterAttrs (name: value: value != null) input
            );

          config_file = pkgs.writers.writeTOML "config.toml" (removeNull cfg.settings);

          package = pkgs.stdenvNoCC.mkDerivation {
            name = "auto-deploy";

            src = ./.;

            nativeBuildInputs = [ pkgs.makeWrapper ];
            buildInputs = [ pkgs.python3 ];

            installPhase = ''
              install -Dm777 nixos-pull-deploy.py $out/bin/nixos-pull-deploy

              wrapProgram $out/bin/nixos-pull-deploy \
                --prefix PATH : ${
                  lib.makeBinPath (
                    with pkgs;
                    [
                      git
                      procps
                      config.system.build.nixos-rebuild
                    ]
                  )
                } \
                --add-flags "-c ${config_file}"
            '';

            meta.mainProgram = "nixos-pull-deploy";
          };
        in
        {
          options.services.nixos-pull-deploy = {
            enable = lib.mkEnableOption "nixos-pull-deploy";

            autoUpgrade = {
              enable = lib.mkEnableOption "automatic upgrades using nixos-pull-deploy";

              startAt = lib.mkOption {
                type = lib.types.str;
                default = "*-*-* 02:00:00";
                description = "When to start automatic updates";
              };
            };

            settings = {
              config_dir = lib.mkOption {
                type = lib.types.str;
                default = "/var/lib/nixos-pull-deploy/repo";
                description = "Path to the local git repo to store the configuration";
              };

              origin = {
                url = lib.mkOption {
                  type = lib.types.str;
                  description = "git url to the upstream repository";
                };

                main = lib.mkOption {
                  type = lib.types.str;
                  description = "Name of the main branch";
                  example = "main";
                };

                testing = lib.mkOption {
                  type = lib.types.str;
                  description = "Prefix for testing branches. The hostname is appended to this prefix.";
                  example = "testing-";
                };

                token = lib.mkOption {
                  type = lib.types.nullOr lib.types.str;
                  default = null;
                  description = "Token to access private git repository via https";
                };

                token_file = lib.mkOption {
                  type = lib.types.nullOr lib.types.str;
                  default = null;
                  description = "File to token to access private git repository via https";
                };
              };
            };
          };

          config = lib.mkIf cfg.enable {
            environment.systemPackages = [ package ];

            systemd = lib.mkIf cfg.autoUpgrade.enable {
              services.nixos-pull-deploy = {
                description = "automatic pull-based nixos deployments";
                script = "${lib.getExe package} run";
                serviceConfig.Type = "exec";
              };

              timers.nixos-pull-deploy = {
                wantedBy = [ "timers.target" ];
                timerConfig.OnCalendar = cfg.autoUpgrade.startAt;
              };
            };
          };
        };
    };
}
