{
  lib,
  ...
}:
{
  options.services.nixos-pull-deploy =
    let
      deploy_modes = [
        "test"
        "switch"
        "boot"
        "reboot"
        "reboot_on_kernel_change"
      ];
    in
    {
      enable = lib.mkEnableOption "nixos-pull-deploy";

      autoUpgrade = {
        enable = lib.mkEnableOption "automatic upgrades using nixos-pull-deploy";

        startAt = lib.mkOption {
          type = lib.types.str;
          default = "*-*-* 02:00:00";
          description = "When to start automatic updates";
        };

        randomizedDelay = lib.mkOption {
          type = lib.types.str;
          default = "10min";
          description = "RandomizedDelaySec for timer";
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

          testing_prefix = lib.mkOption {
            type = lib.types.str;
            default = "testing/";
            description = "Prefix for testing branches. The hostname is appended to this prefix.";
          };

          testing_separator = lib.mkOption {
            type = lib.types.str;
            default = "/";
            description = "Separator between hostnames in testing branch name";
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
          description = ''
            Path to executable to run before and after deployment.

            The following environment variables are available:
            - DEPLOY_STATUS:
              - pre: deployment is about to happen
              - success: deployment succeeded
              - failed: deployment failed (either evaluation or build failure or it was automatically rolled back)
            - DEPLOY_TYPE: Type of branch that is being deployed, either "main" or "testing"
            - DEPLOY_MODE: Deployment mode, can be one of ${lib.concatStringsSep ", " deploy_modes}
            - DEPLOY_COMMIT: Hash of the deployed commit
            - DEPLOY_COMMIT_MESSAGE: Message of the deployed commit
            - DEPLOY_SUCCESS_COMMIT: Hash of the last successfully deployed commit or an empty string
            - DEPLOY_SUCCESS_COMMIT_MESSAGE: Message of the last successfully deployed commit or an empty string
            - DEPLOY_SCHEDULED: 1 if the deployment is running inside of a systemd service, 0 if it is interactive
          '';
          example = ''
            pkgs.writeShellScript "hook.sh" '''
              if [[ "$DEPLOY_STATUS" == 'success' ]] then
                echo "$DEPLOY_MODE deployment of commit $DEPLOY_COMMIT succeeded";;
              elif [[ "$DEPLOY_STATUS" == 'failed' ]]
                echo 'deployment failed'
              fi
            '''
          '';
        };

        deploy_modes = {
          main = lib.mkOption {
            type = lib.types.enum deploy_modes;
            default = "switch";
            description = "Mode to deploy the main branch with";
          };

          testing = lib.mkOption {
            type = lib.types.enum deploy_modes;
            default = "test";
            description = "Mode to deploy the testing branch with";
          };
        };

        magic_rollback_timeout = lib.mkOption {
          type = lib.types.int;
          default = 3;
          description = "Duration to wait for network to become available after deployment in seconds";
        };

        fetch_retries = lib.mkOption {
          type = lib.types.int;
          default = 1;
          description = "How often to retry fetching from the remote";
        };

        build_remotes = lib.mkOption {
          type = lib.types.listOf lib.types.str;
          default = [ "local" ];
          description = "Remotes to evaluate and build the configuration on";
          example = [
            "root@example.com:123"
            "local"
          ];
        };
      };
    };
}
