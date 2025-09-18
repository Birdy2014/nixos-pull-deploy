{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";

  outputs =
    { self, nixpkgs }:
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

      packages = forAllSupportedSystems (pkgs: {
        default = pkgs.callPackage (
          {
            lib,
            python3Packages,
            git,
            procps,
            nixos-rebuild,
          }:

          python3Packages.buildPythonApplication {
            pname = "nixos-pull-deploy-unwrapped";
            version = "0.1.0";

            src = ./.;

            pyproject = true;
            build-system = [ python3Packages.setuptools ];

            nativeCheckInputs = [
              python3Packages.unittestCheckHook
              git
            ];

            makeWrapperArgs = [
              "--prefix PATH : ${
                lib.makeBinPath [
                  git
                  procps
                  nixos-rebuild
                ]
              }"
            ];

            meta.mainProgram = "nixos-pull-deploy";
          }
        ) { };
      });

      checks = forAllSupportedSystems (pkgs: {
        default = self.packages.${pkgs.system}.default;
      });

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

          configFile = pkgs.writers.writeTOML "config.toml" (removeNull cfg.settings);

          package =
            let
              genericPackage = self.packages.${pkgs.system}.default.override {
                nixos-rebuild = config.system.build.nixos-rebuild;
              };
            in
            pkgs.stdenvNoCC.mkDerivation {
              name = "nixos-pull-deploy";

              nativeBuildInputs = [ pkgs.makeWrapper ];

              dontUnpack = true;

              installPhase = ''
                mkdir -p $out/bin
                makeWrapper ${genericPackage}/bin/nixos-pull-deploy $out/bin/nixos-pull-deploy \
                  --set DEPLOY_CONFIG "${configFile}"
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

              hook = lib.mkOption {
                type = lib.types.nullOr lib.types.path;
                default = null;
                description = "Path to executable to run after deployment";
                example = ''
                  pkgs.writeShellScript "hook.sh" '''
                    if [[ "$DEPLOY_SUCCESS" == '1' ]] then
                      echo "$DEPLOY_MODE deployment of commit $DEPLOY_COMMIT succeeded";;
                    else
                      echo 'deployment failed'
                    fi
                  '''
                '';
              };

              deploy_modes = {
                main = lib.mkOption {
                  type = lib.types.oneOf [
                    "test"
                    "switch"
                  ];
                  default = "switch";
                  description = "Mode to deploy the main branch with";
                };

                testing = lib.mkOption {
                  type = lib.types.oneOf [
                    "test"
                    "switch"
                  ];
                  default = "test";
                  description = "Mode to deploy the testing branch with";
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
