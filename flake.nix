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

                # One place to bump the version: pyproject.toml. Never tag an
                # image `latest` — this project exists to make versions explicit.
                version = (builtins.fromTOML
                    (builtins.readFile ./pyproject.toml)).project.version;

                # An OCI image is a Linux image whatever the host is, so it is
                # built from Linux nixpkgs even when evaluated on darwin.
                oci_system = builtins.replaceStrings ["darwin"] ["linux"] system;

                # Build the image for one Linux system. `mkOci "x86_64-linux"` is
                # the nix answer to `docker buildx --platform`, except there is no
                # emulation: each target needs a builder that can run it.
                mkOci = oci_sys:
                    let
                        p = import nixpkgs { system = oci_sys; };

                        # The runtime deps from pyproject.toml, resolved by nix
                        # rather than uv. Keep this list equal to
                        # [project.dependencies] — the image has no uv and never
                        # syncs, so nothing else will catch a drift.
                        py = p.python313.withPackages (ps: builtins.attrValues {
                            inherit (ps)
                                requests
                                python-dotenv
                                ratelimit
                                backoff
                                pyyaml;
                        });

                        # Just the source. No wheel: the packages import as top
                        # level names off hermetica/, so PYTHONPATH is the whole
                        # install.
                        src = p.runCommand "hermetica-src" { } ''
                            mkdir -p $out/app
                            cp -r ${./hermetica} $out/app/hermetica
                        '';
                    in
                    p.dockerTools.buildLayeredImage {
                        name = "hermetica";
                        tag = version;
                        contents = [
                            p.cacert          # HTTPS from python
                            p.bashInteractive
                            p.coreutils
                            py
                            src
                        ];
                        config = {
                            Cmd = [ "${py}/bin/python" "-m" "api.server" ];
                            WorkingDir = "/app";
                            ExposedPorts = { "8080/tcp" = { }; };
                            # DB and LOGS are deliberately NOT set. The code
                            # already defaults them to db/ and logs/ relative to
                            # the working directory, so /app/db and /app/logs
                            # mirror the repo layout for free. Setting them here
                            # would be a second declaration of the same thing.
                            Env = [
                                "PYTHONPATH=/app/hermetica"
                                "PYTHONDONTWRITEBYTECODE=1"
                                "PYTHONUNBUFFERED=1"
                                # The one real override: the code defaults to
                                # 127.0.0.1, which no sibling container could
                                # reach. Safe ONLY while the port stays
                                # unpublished — there is no auth.
                                "API_HOST=0.0.0.0"
                                "SSL_CERT_FILE=${p.cacert}/etc/ssl/certs/ca-bundle.crt"
                            ];
                            Volumes = { "/app/db" = { }; "/app/logs" = { }; };
                            Labels = {
                                "org.opencontainers.image.title" = "hermetica";
                                "org.opencontainers.image.version" = version;
                            };
                        };
                    };

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

                # ---------------------------------------------------------------
                # OCI IMAGES — the API, and nothing that only scribe needs.
                # ---------------------------------------------------------------
                # Serves the API by default; override the command to run a pull:
                #   docker run hermetica:VERSION python -m chronos.chronos
                # No pandoc and no tex: scribe has no entry point, and they cost
                # ~500 MB. Add them to `contents` when it does.
                #
                # Say nothing and you build for this machine's architecture; name
                # a target and you build for that one:
                #   nix build .#oci                 -> host arch, Linux
                #   nix build .#oci-x86_64-linux    -> a typical server
                #   nix build .#oci-aarch64-linux   -> an arm server
                #
                # Every target is Linux, so on darwin these need a Linux
                # builder — `nix.linux-builder.enable = true` in nix-darwin, or a
                # remote one. Without one nix stops with
                # `Required system: 'x86_64-linux'`, which is correct, not broken:
                # an image full of Mach-O binaries would be useless.
                packages = {
                    oci = mkOci oci_system;
                    oci-x86_64-linux = mkOci "x86_64-linux";
                    oci-aarch64-linux = mkOci "aarch64-linux";
                    default = mkOci oci_system;
                };
            }
            
        );

}
