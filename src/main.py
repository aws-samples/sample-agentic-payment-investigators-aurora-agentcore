# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
# Sample code, non-production. See README.md for the full disclaimer.
"""AgentCore CodeZip entrypoint — bundle root (src/), imports the package
ABSOLUTELY. The adapter's relative imports (`from .agents import …`) require
loading as a package member; a runtime that executed investigator/adapter.py
as a top-level script would die on them. This shim is the entrypoint the
deploy registers (setup_deploy.sh) — everything real lives in the package.
"""
from investigator.adapter import app

if __name__ == "__main__":
    app.run()
