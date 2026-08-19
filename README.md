# HERMETICA

Protocol Version Control and Pipeline Composition. 

Protocols.io is great but it does have one slight little issue: it does not explicitely enforce version control. Protocol history is tracked and you can see differences but all of this is silent. Someone makes a change and doesn't tell you? You are now using a different protocol than the one you thought you were. A project gets parked for a year and the client comes back asking for the protocol you used for their Materials section? How certain are you that the version that is in your workspace is the one you actually used without manually checking the change history? 

Protocol versioning is based on human discipline which starts to break apart the moment your team is underpressure to deliver or they have to handle to many requests to keep track of what is going on upstream.

Hermetica provides a simple system to keep track of protocol changes by regularly checking upstream protocol and seeing if anything has changed. In addition and more importantly, Hermetica provide a `lock` file which tells you exactly which version of the protocol you used. No more guessing, you now have a contract that can be used 2 years down the line to get the exact protocol you used. 

In addition, protocols are more often than not part of a workflow which are composed of multiple protocols. Hermetica also tracks workflow composition and provide the workflow was part of the `lock` file. Not only do you know which protocols you used but also in which order.

NOTE: There is a protocols.io MCP server. Not sure what to do with this information at the moment but worth keeping in mind.

# Guardrails

## Agent Rules

Will add some agent rules and context for agentic development in the future. I am still refining those rules.

## Environments

The project works on a multi-tier level for development purposes. Currently, only option 1 and 3 are provided. 

1. `uv` only. All python dependencies are stated in the `pyproject.toml` and can be run using the `uv` / `venv` virtual environments. Certain system dependencies are required but that's on you to add them (`pandoc` for example). 

2. `docker` + `uv`. We will provide a `docker` container for this project which will contain all the python dependencies stated in the `pyproject.toml`. 

3. `nix` + `uv`. All code can be run in a `nix develop` shell which handles system dependencies and will install python dependencies with `uv`

4. `nix` + `OCI` + `uv`. The `nix flake` contains development shell instruction but also OCI image build instruction. Image can be built directly from a version pinned nix flake.

NOTE: We will also provide a `PIXI` approach in the future for those who prefer a conda like environment.

## Pre-commit hooks

This directory contains pre-commit hooks that will trigger on a push to main. It will run `ruff` (check and lint) and `detect-secrets`. `PyTest` does not currently run in the pre-commit hook but will run through GitHub Actions. It might be added in the future. The goal is to make sure that we are confident in the code that we push and shared before doing so. 


# AI Use

This project used Claude code (Opus 4.8 and Opus 5) to write code, unit tests and documentation. However, this is not a "vibe coded" project. Outline code was written by
a human, expanded on or fixed by AI, and then vetted by human again. And let me tell, you it needed vetting. Did you know that Claude tried to circumvent my rules about writing page comments and docs, by writing a python class that would contain a bunch of strings with its comments and docs?