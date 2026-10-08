{
  lib,
  rustPlatform,
  makeWrapper,
  pkg-config,
  libgit2,
  nix,
}:

rustPlatform.buildRustPackage {
  pname = "nixos-pull-deploy-unwrapped";
  version = "0.2.1";

  src = ../.;

  cargoHash = "sha256-uQdhBfcviOMX6RBWP0u0TWfT2mdXiEjlvjEfBNDlOSc=";

  nativeBuildInputs = [
    makeWrapper
    pkg-config
  ];
  buildInputs = [ libgit2 ];

  postInstall = ''
    wrapProgram $out/bin/nixos-pull-deploy \
      --prefix PATH : ${lib.makeBinPath [ nix ]}
  '';

  env.LIBGIT2_NO_VENDOR = true;

  meta.mainProgram = "nixos-pull-deploy";
}
