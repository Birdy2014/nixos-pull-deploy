## services\.nixos-pull-deploy\.enable



Whether to enable nixos-pull-deploy\.



*Type:*
boolean



*Default:*
` false `



*Example:*
` true `



## services\.nixos-pull-deploy\.autoUpgrade\.enable

Whether to enable automatic upgrades using nixos-pull-deploy\.



*Type:*
boolean



*Default:*
` false `



*Example:*
` true `



## services\.nixos-pull-deploy\.autoUpgrade\.randomizedDelay



RandomizedDelaySec for timer



*Type:*
string



*Default:*
` "10min" `



## services\.nixos-pull-deploy\.autoUpgrade\.startAt



When to start automatic updates



*Type:*
string



*Default:*
` "*-*-* 02:00:00" `



## services\.nixos-pull-deploy\.settings\.config_dir



Path to the local git repo to store the configuration



*Type:*
string



*Default:*
` "/var/lib/nixos-pull-deploy/repo" `



## services\.nixos-pull-deploy\.settings\.deploy_modes\.main



Mode to deploy the main branch with



*Type:*
one of “test”, “switch”, “boot”, “reboot”, “reboot_on_kernel_change”



*Default:*
` "switch" `



## services\.nixos-pull-deploy\.settings\.deploy_modes\.testing



Mode to deploy the testing branch with



*Type:*
one of “test”, “switch”, “boot”, “reboot”, “reboot_on_kernel_change”



*Default:*
` "test" `



## services\.nixos-pull-deploy\.settings\.hook



Path to executable to run before and after deployment\.

The following environment variables are available:

 - DEPLOY_STATUS:
   
    - pre: deployment is about to happen
    - success: deployment succeeded
    - failed: deployment failed (either evaluation or build failure or it was automatically rolled back)
 - DEPLOY_TYPE: Type of branch that is being deployed, either “main” or “testing”
 - DEPLOY_MODE: Deployment mode, can be one of test, switch, boot, reboot, reboot_on_kernel_change
 - DEPLOY_COMMIT: Hash of the deployed commit
 - DEPLOY_COMMIT_MESSAGE: Message of the deployed commit
 - DEPLOY_SUCCESS_COMMIT: Hash of the last successfully deployed commit or an empty string
 - DEPLOY_SUCCESS_COMMIT_MESSAGE: Message of the last successfully deployed commit or an empty string
 - DEPLOY_SCHEDULED: 1 if the deployment is running inside of a systemd service, 0 if it is interactive



*Type:*
null or absolute path



*Default:*
` null `



*Example:*

```
''
  pkgs.writeShellScript "hook.sh" '''
    if [[ "$DEPLOY_STATUS" == 'success' ]] then
      echo "$DEPLOY_MODE deployment of commit $DEPLOY_COMMIT succeeded";;
    elif [[ "$DEPLOY_STATUS" == 'failed' ]]
      echo 'deployment failed'
    fi
  '''
''
```



## services\.nixos-pull-deploy\.settings\.magic_rollback_timeout



Duration to wait for network to become available after deployment in seconds



*Type:*
signed integer



*Default:*
` 3 `



## services\.nixos-pull-deploy\.settings\.origin\.main



Name of the main branch



*Type:*
string



*Example:*
` "main" `



## services\.nixos-pull-deploy\.settings\.origin\.testing_prefix



Prefix for testing branches\. The hostname is appended to this prefix\.



*Type:*
string



*Default:*
` "testing/" `



## services\.nixos-pull-deploy\.settings\.origin\.testing_separator



Separator between hostnames in testing branch name



*Type:*
string



*Default:*
` "/" `



## services\.nixos-pull-deploy\.settings\.origin\.token



Token to access private git repository via https



*Type:*
null or string



*Default:*
` null `



## services\.nixos-pull-deploy\.settings\.origin\.token_file



File to token to access private git repository via https



*Type:*
null or string



*Default:*
` null `



## services\.nixos-pull-deploy\.settings\.origin\.url



git url to the upstream repository



*Type:*
string


