{
  lib,
  python3Packages,
  git,
  nix,
}:

python3Packages.buildPythonApplication {
  pname = "nixos-pull-deploy-unwrapped";
  version = "0.1.0";

  src = ../.;

  pyproject = true;
  build-system = [ python3Packages.setuptools ];

  nativeCheckInputs = [
    python3Packages.unittestCheckHook
    git
  ];

  propagatedBuildInputs = map lib.getBin [
    git
    nix
  ];

  meta.mainProgram = "nixos-pull-deploy";
}
