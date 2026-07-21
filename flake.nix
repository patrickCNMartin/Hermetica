{
    description = "Protocol Version Control and composition - HERMETICA";
    inputs = {
        nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
        flake-utils.url = "github:numtide/flake-utils";
    };
    outputs = {self, nixpkgs,flake-utils}:
        flake-utils.lib.eachDefaultSystem(system:
            let pkgs = import nixpkgs {inherit system;};
                # THIS LIST IS FOR NON-PYTHON SYSTEM DEPENDENCIES ONLY.
                # Every Python package and every Python-based tool (pytest,
                # ruff, pre-commit, detect-secrets) belongs in pyproject.toml,
                # which is the single declaration shared by the uv / nix / pixi
                # paths. Adding a Python application here re-creates the
                # PYTHONPATH shadowing bug: Nix propagates its whole closure
                # (e.g. its own pytest) into the shell, where it overrides the
                # uv venv on sys.path.
                #
                # No native build toolchain (gcc/gfortran/cmake/pkg-config)
                # either — current deps are pure-Python wheels. This is
                # deliberately just the toolchain needed to bootstrap the venv.
                system_deps = builtins.attrValues {
                    inherit (pkgs)
                        which
                        git
                        uv;
                };
                python_base = pkgs.python313;
                oci_deps = [
                    python_base
                    pkgs.uv
                    pkgs.cacert # Essential for HTTPS requests within python/uv
                    pkgs.bashInteractive
                    pkgs.coreutils
                ];
    
            in {
                devShells.default = pkgs.mkShell {
                    buildInputs = system_deps ++ [python_base];
                    shellHook = ''
                        echo "====> HERMETICA - Preparing DEV SHELL <===="

                        export UV_PYTHON="${python_base}/bin/python3"
                        export VIRTUAL_ENV=".venv"

                        if [ ! -d ".venv" ]; then
                            echo "====> Creating uv venv <===="
                            uv venv .venv --python "${python_base}/bin/python3"
                        fi

                        source .venv/bin/activate

                        if [ -f "pyproject.toml" ]; then
                            echo "====> Syncing deps (incl. dev tooling) <===="
                            uv sync --extra dev
                        fi
                    '';
                };
            }
            
        );

}