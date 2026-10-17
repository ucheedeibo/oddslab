---
name: Python package setup
description: Replit Python runtime and requirements-file behavior when installing dependencies.
---

For Python projects in this workspace, a base Python runtime may not include pip and may reject global installs as externally managed. A pip-enabled Python Tools runtime worked for installing project dependencies. The package installer may also duplicate existing entries in `requirements.txt`.

**Why:** The base runtime install failed without pip; after switching to a pip-enabled runtime, installation succeeded but duplicated the original requirements.

**How to apply:** Before installing Python packages, use a pip-enabled runtime. After installation, compare an existing `requirements.txt` with its source and remove installer-added changes if preserving the original file matters.
