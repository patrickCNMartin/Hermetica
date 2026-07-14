{
    description = "Protocol Version Control and composition - HERMETICA";
    inputs = {
        nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
        flake-utils.url = "github:numtide/flake-utils";
    };
    outputs = {self, nixpkgs,flake-utils}:
        flake-utils.lib.eachDefaultSystem(system:
            let pkgs = import nixpkgs {inherit system;};
                system_deps = builtins.attrValues {
                    inherit (pkgs)
                        gcc
                        gfortran
                        cmake
                        pkg-config
                        which
                        pre-commit
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
                            echo "====> Syncing deps from pyproject.toml <===="
                            uv sync
                        fi                        
                    '';
                };
            }
            
        );

}