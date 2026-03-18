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
        default = pkgs.callPackage ./nix/package.nix { };

        options-documentation =
          let
            eval = pkgs.lib.evalModules {
              modules = [ (import ./nix/module-options.nix) ];
            };
            optionsDoc = pkgs.nixosOptionsDoc {
              options = pkgs.lib.filterAttrs (name: value: name != "_module") eval.options;
            };
          in
          pkgs.runCommand "options.md" { } ''
            cp ${optionsDoc.optionsCommonMark} $out
          '';
      });

      checks = forAllSupportedSystems (pkgs: {
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      });

      devShells = forAllSupportedSystems (pkgs: {
        default = self.packages.${pkgs.stdenv.hostPlatform.system}.default.overrideAttrs (attrs: {
          nativeBuildInputs = attrs.nativeBuildInputs ++ [ pkgs.black ];
        });
      });

      nixosModules.default = nixpkgs.lib.modules.importApply ./nix/module.nix { inherit self; };
    };
}
