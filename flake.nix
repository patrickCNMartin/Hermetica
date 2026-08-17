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
                # paths. 
                system_deps = builtins.attrValues {
                    inherit (pkgs)
                        which
                        pandoc
                        git
                        uv;
                };
                # get the tex packages to generate full math reports 
                # Weird unicode stuff happening otherwise.
                tex = pkgs.texliveSmall.withPackages (ps: [
                    ps.dejavu
                    ps.lualatex-math
                ]);
                # Fix python version
                python_base = pkgs.python313;
                # Stuff that is specifically required to build the OCIs
                # Don't put shit here that you will be pulling from other args
                oci_deps = [
                    pkgs.cacert # Essential for HTTPS requests within python/uv
                    pkgs.bashInteractive
                    pkgs.coreutils
                ];
    
            in {
                devShells.default = pkgs.mkShell {
                    buildInputs = system_deps ++ [tex] ++ [python_base];
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