{
  lib,
  rustPlatform,
  makeWrapper,
  pkg-config,
  openssl,
  nix,
}:

rustPlatform.buildRustPackage {
  pname = "nixos-pull-deploy-unwrapped";
  version = "0.2.0";

  src = ../.;

  cargoHash = "sha256-sD4nGod3E2phiEuN1v8SSjGr3QgiLHMStwWhXL6Gu5Y=";

  nativeBuildInputs = [ makeWrapper pkg-config ];
  buildInputs = [ openssl ];

  postInstall = ''
    wrapProgram $out/bin/nixos-pull-deploy \
      --prefix PATH : ${lib.makeBinPath [ nix ]}
  '';

  meta.mainProgram = "nixos-pull-deploy";
}
