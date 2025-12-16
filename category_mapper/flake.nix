{
  inputs = {
    devenv.url = "github:cachix/devenv";
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs, devenv, ... }@inputs:
    let
      system = "x86_64-linux";

      pkgs = import nixpkgs {
        inherit system;
        config = { allowUnfree = true; };
        overlays = [ ];
      };

    in {

      devShell.${system} = devenv.lib.mkShell {
        inherit inputs pkgs;

        modules = [
          ({ pkgs, config, ... }: {

            languages.nix.enable = true;
            languages.nix.lsp.package = pkgs.nil;

            # https://devenv.sh/supported-languages/python/
            languages.python = {
              enable = true;
              package = pkgs.python312;
              uv = {
                enable = true;
                package = pkgs.uv;
                sync.enable = true;
              };
            };

            packages = with pkgs; [
                python312Packages.isort
                python312Packages.black
                python312Packages.ruff
                python312Packages.reuse
                mrustc-minicargo
            ];

            env.PYTHONPATH = "$PYTHONPATH:${self}";
            env.UV_PYTHON = "${pkgs.python312}";
            env.UV_PYTHON_PREFERENCE = "only-system";

            # Add any missing library pkg to PATH
            env.LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
              pkgs.stdenv.cc.cc
              pkgs.zlib
              pkgs.glib
            ];

          })
        ];
      };
    };
}
