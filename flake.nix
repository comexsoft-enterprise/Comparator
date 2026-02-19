{
  description = "comparator";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    devenv.url = "github:cachix/devenv";
    niv.url = "github:monk3yd/niv";
  };

  outputs =
    {
      devenv,
      nixpkgs,
      niv,
      ...
    }@inputs:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
        config.allowUnfree = true;
        overlays = [ ];
      };

      # Packages added to all modules development environments.
      # IMPORTANT: keep this list free of Python packages to avoid `PYTHONPATH`
      # conflicts with uv-managed venv dependencies.
      basePackages = with pkgs; [
        pulumi
        pulumiPackages.pulumi-python

        sops
        age
        ssh-to-age
        yq

        jq
        visidata

        niv.packages.${system}.default
      ];

      # Configure python using devenv nix configuration
      basePythonConf = {
        enable = true;
        package = pkgs.python313;
        manylinux.enable = true;
        uv = {
          enable = true;
          package = pkgs.uv;
          sync.enable = true;
        };
      };

      baseEnvVars = {
        # Prevent devenv from injecting Nix python site-packages into runtime.
        # This repo uses `uv` to manage Python dependencies.
        PYTHONPATH = "";
        PYTHONNOUSERSITE = "1";
        PYTHONHOME = "";

        # Setup AWS
        PROJECT_NAME = "comparator";

        # FIX Change to dev PROFILE
        AWS_PROFILE = "monk3yd-comexsoft";
        AWS_ACCOUNT_ID = "990187902980";
        AWS_REGION = "us-east-1";

        # AWS credentials (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY)
        # are loaded from secrets.yaml via secretsLoader
        # This should be used only when running in container within ECS, not for local/test runs

        # Database defaults (for local API + SSH tunnels)
        # Use `./connect2eyservices.sh` to forward ports from EC2.
        MONGO_URI = "localhost:27017";
        MONGO_USER1 = "admin";
        MONGO_USER1_PASSWORD = "admin";
        MONGO_AUTH_SOURCE = "admin";
        MONGO_DATABASE = "test";

        # Neo4j (bolt protocol)
        NEO4J_URI = "bolt://localhost:7687";
        NEO4J_USER = "neo4j";
        NEO4J_PASSWORD = "admin";
        NEO4J_DATABASE = "neo4j";

        POSTGRES_HOST = "localhost";
        POSTGRES_PORT = "5432";
        POSTGRES_DB = "myappdb";
        POSTGRES_USER = "admin";
        POSTGRES_PASSWORD = "admin";

        # Setup UV for nix compatibility
        UV_LINK_MODE = "copy";
        # Allow devenv's `uv sync` task to run
        UV_NO_SYNC = "0";
        UV_PYTHON_DOWNLOADS = "never";

        # Prefect
        # PREFECT_API_URL = "https://prefect.raftel.cl:api";
        # PREFECT_API_URL = "http://comexsoft:4200/api";

        PREFECT_API_URL = "http://comexsoft-prod-alb-78678af-1558930085.us-east-1.elb.amazonaws.com/api";

        # ECS execution context (from `pulumi stack output --json`)
        PREFECT_ECR_REPOSITORY = "990187902980.dkr.ecr.us-east-1.amazonaws.com/comexsoft-prod-ecr-649493f";
        PREFECT_ECS_CLUSTER = "prefect-cluster";
        PREFECT_VPC_ID = "vpc-0ea48a8d1995cf5c8";
        # Private subnets (with NAT): preferred for production
        PREFECT_SUBNETS = ''["subnet-01df359b4892fbc21","subnet-070cbd2f9c16e236c"]'';
        PREFECT_SECURITY_GROUP = "sg-0a01508c2dc292254";
        PREFECT_ECS_EXECUTION_ROLE_ARN = "arn:aws:iam::990187902980:role/comexsoft-prod-ecs-task-execution-role-86f3933";
        PREFECT_ECS_TASK_ROLE_ARN = "arn:aws:iam::990187902980:role/comexsoft-prod-ecs-task-execution-role-86f3933";

        # Image tag used by `prefect deploy` (must be Docker-tag-safe)
        # NOTE: bash expansion doesn't work in Nix strings; defaulting is done in `secretsLoader`.
        PREFECT_TAG = "";
        # Ensure Prefect CLI talks to the ALB, not container DNS
        PREFECT_API_URL_OVERRIDE = "";

        PREFECT_LOGGING_EXTRA_LOGGERS = "crawlee";

        # Azure agent
        AZURE_API_TYPE = "azure";
        AZURE_API_VERSION = "2025-08-07";
        AZURE_RESOURCE_NAME = "devel-mjx2crb7-eastus2";
        AZURE_API_BASE = "https://devel-mjx2crb7-eastus2.openai.azure.com/";
        AZURE_DEPLOYMENT_NAME = "gpt-5.2"; # gpt-5.2

        # Azure OpenAI (DO NOT commit secrets here; set via your env/secrets manager)
        AZURE_OPENAI_API_TYPE = "azure";
        # Manejado en .envrc por seguridad
        # AZURE_OPENAI_API_KEY = "";
        AZURE_OPENAI_API_VERSION = "2025-03-01-preview";
        AZURE_OPENAI_API_BASE = "https://comexsoftllm.openai.azure.com/";
        AZURE_OPENAI_DEPLOYMENT_NAME = "gpt-5-nano";
        AZURE_OPENAI_API_ENDPOINT = "https://comexsoftllm.openai.azure.com/";
      };

      baseLibraryPath = {
        # Add any missing library pkg to PATH
        LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
          pkgs.stdenv.cc.cc
          pkgs.zlib
        ];
      };

      secretsLoader = ''
        export REPO_ROOT=$(git rev-parse --show-toplevel)

        # Ensure we use the Nix-provided `uv` binary (the venv may contain a
        # dynamically-linked `uv` that can't run under Nix's stub-ld setup).
        # Prefer Nix-provided `uv` in this shell.
        # Note: activating the venv adds its own `uv` shim first on PATH,
        # which may be dynamically linked and fail under Nix; re-prepend after activation.
        export PATH="${pkgs.uv}/bin:$PATH"
        hash -r

        # Deps are synced by devenv's python/uv integration.
        # Keep this hook focused on secrets + venv activation.
        command -v uv
        . .devenv/state/venv/bin/activate

        # Ensure the venv controls Python deps (avoid mixing Nix-provided
        # site-packages via PYTHONPATH, which can break locked uv deps).
        unset PYTHONPATH
        unset PYTHONHOME
        export PYTHONNOUSERSITE=1

        export PATH="${pkgs.uv}/bin:$PATH"
        alias uv="${pkgs.uv}/bin/uv"
        hash -r
      '';

    in
    {
      devShells.${system} = {

        # Root dev environment activated by default
        default = devenv.lib.mkShell {
          inherit inputs pkgs;

          modules = [
            (
              { pkgs, ... }:
              {
                # https://devenv.sh/supported-languages/nix/
                languages.nix.enable = true;
                languages.nix.lsp.package = pkgs.nil;

                # https://devenv.sh/supported-languages/python/
                languages.python = basePythonConf;

                packages = basePackages;

                env = baseEnvVars // baseLibraryPath;

                enterShell = ''
                  echo "🔐 Loading default devenv environment..."

                  # Ensure venv owns Python deps (no Nix site-packages)
                  unset PYTHONPATH
                  unset PYTHONHOME
                  export PYTHONNOUSERSITE=1

                  ${secretsLoader}
                '';

              }
            )
          ];

        };

      };

    };
}
