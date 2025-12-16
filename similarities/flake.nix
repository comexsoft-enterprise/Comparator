{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    nixpkgs-stable.url = "github:nixos/nixpkgs/nixos-23.11";
    devenv.url = "github:cachix/devenv";
  };

  outputs =
    {
      self,
      nixpkgs,
      nixpkgs-stable,
      devenv,
      ...
    }@inputs:
    let
      system = "x86_64-linux";

      # Create overlay to bring in stable numpy
      overlay-stable-numpy = final: prev: {
        python39Packages = prev.python39Packages // {

          numpy =
            (import nixpkgs-stable {
              inherit system;
              config.allowUnfree = true;
            }).python39Packages.numpy;

          # Use faiss from stable nixpkgs
          faiss =
            (import nixpkgs-stable {
              inherit system;
              config.allowUnfree = true;
            }).python39Packages.faiss;

        };
      };

      pkgs = import nixpkgs {
        inherit system;
        config = {
          allowUnfree = true;
        };
        overlays = [ overlay-stable-numpy ];
      };
    in
    {

      devShell.${system} = devenv.lib.mkShell {
        inherit inputs pkgs;

        modules = [
          (
            { pkgs, config, ... }:
            {
              # https://devenv.sh/supported-languages/python/
              languages.python = {
                enable = true;
                package = pkgs.python39; # Use Python 3.9 consistently
                uv = {
                  enable = true;
                  package = pkgs.uv;
                  sync.enable = true;
                };
              };

              packages = with pkgs; [
                python39Packages.requests
                python39Packages.numpy # This now comes from stable nixpkgs via the overlay
                python39Packages.faiss
              ];

              env.AWS_PROFILE = "monk3yd-comexsoft";
              env.AWS_ACCOUNT_ID = "990187902980";
              env.AWS_REGION_NAME = "us-east-1";
              env.AWS_ACCESS_KEY_ID = "";
              env.AWS_SECRET_ACCESS_KEY = "";

              # NOTE
              # Always update your python PATH!
              env.PYTHONPATH = "$PYTHONPATH:${self}";

              # Ensure UV uses Python 3.9
              env.UV_PYTHON = "${pkgs.python39}";
              env.UV_PYTHON_PREFERENCE = "only-system";

              # Add any missing library pkg to PATH
              env.LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
                pkgs.stdenv.cc.cc
                pkgs.zlib
                # pkgs.glib
                # pkgs.xorg.libX11
                # pkgs.libuv
              ];

              enterShell = ''
                rm -rf .devenv && echo " * Deleted previous .devenv"
                uv venv -p ${pkgs.python39} -v
                source .devenv/bin/activate
              '';
            }
          )
        ];
      };
    };
}
