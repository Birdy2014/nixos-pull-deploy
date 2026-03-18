{
  lib,
  python3Packages,
  git,
  procps,
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
    procps
    nix
  ];

  meta.mainProgram = "nixos-pull-deploy";
}
