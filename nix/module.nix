{ self }:
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
      genericPackage = self.packages.${pkgs.stdenv.hostPlatform.system}.default.override {
        nix = config.nix.package;
      };

      makeWrapperArgs = [
        "--set DEPLOY_CONFIG ${configFile}"
      ]
      ++ (lib.optional (cfg.settings.build_remotes != [ "local" ])
        "--prefix PATH : ${lib.makeBinPath [ pkgs.openssh ]}"
      );
    in
    pkgs.stdenvNoCC.mkDerivation {
      name = "nixos-pull-deploy";

      nativeBuildInputs = [ pkgs.makeWrapper ];

      dontUnpack = true;

      installPhase = ''
        mkdir -p $out/bin
        makeWrapper ${genericPackage}/bin/nixos-pull-deploy $out/bin/nixos-pull-deploy \
          ${lib.concatStringsSep " " makeWrapperArgs}
      '';

      meta.mainProgram = "nixos-pull-deploy";
    };
in
{
  imports = [ ./module-options.nix ];

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ package ];

    systemd = lib.mkIf cfg.autoUpgrade.enable {
      services.nixos-pull-deploy = {
        description = "automatic pull-based nixos deployments";
        script = "${lib.getExe package} run";
        serviceConfig.Type = "exec";
        restartIfChanged = false;
      };

      timers.nixos-pull-deploy = {
        wantedBy = [ "timers.target" ];
        wants = [ "network-online.target" ];
        after = [ "network-online.target" ];
        timerConfig = {
          OnCalendar = cfg.autoUpgrade.startAt;
          Persistent = true;
          RandomizedDelaySec = cfg.autoUpgrade.randomizedDelay;
        };
      };
    };
  };
}
