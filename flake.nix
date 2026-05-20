{
    description = "Protocol Version Control and composition";
    input = {
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
                        git;
                };
                python_deps = pkgs.python313.withPackages (packages: with packages; [
                    requests
                    django
                    detect-secrets
                    ruff
                    python-dotenv
                    python-dateutil

                ]);
            in {
                devShells.default = pkgs.mkShell {
                    buildInputs = system_deps ++ [python_deps];
                    shellHook = ''
                        echo "WETWARE SHELL READY"

                    ''
                };
            }
            
        );

}