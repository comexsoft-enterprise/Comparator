{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    devenv.url = "github:cachix/devenv";
    nixos-generators = {
      url = "github:nix-community/nixos-generators";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      nixpkgs,
      devenv,
      nixos-generators,
      ...
    }@inputs:
    let
      system = "x86_64-linux";

      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
      };

    in
    {
      devShell.${system} = devenv.lib.mkShell {
        inherit inputs pkgs;

        modules = [
          (
            { pkgs, ... }:
            {
              languages.nix.enable = true;
              languages.nix.lsp.package = pkgs.nil;

              # Minimal: infra tooling. App deps remain managed by repo root.
              packages = with pkgs; [
                jq
                git
                mosh

                pulumi
                pulumiPackages.pulumi-python
              ];

              env.PROJECT_NAME = "comparator";

              enterShell = ''
                export REPO_ROOT=$(git rev-parse --show-toplevel)
                cd "$REPO_ROOT"
              '';
            }
          )
        ];
      };

      # Buildable NixOS system for the EC2 node.
      nixosConfigurations.ec2 = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [
          ./configuration.nix
        ];
        specialArgs = { inherit inputs; };
      };

      # Optional: image generator (not wired into Pulumi yet).
      packages.${system}.amazon = nixos-generators.nixosGenerate {
        system = system;
        format = "amazon";
        modules = [
          # ./configuration.nix
        ];
      };
    };
}
