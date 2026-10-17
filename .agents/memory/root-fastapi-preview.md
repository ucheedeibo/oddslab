---
name: Root FastAPI web preview
description: Exposing a repository-root FastAPI app through a web artifact in this pnpm workspace.
---

When a web artifact serves the repository-root FastAPI app, map its service to `/` on port 8000 and add Uvicorn's `--app-dir ../../`; the managed artifact command starts from the artifact directory, so the root `app` package is otherwise not importable. Remove or disable any standalone workflow that also binds port 8000, and point the project run workflow at the managed web artifact.

**Why:** The first managed workflow could not import `app` from its artifact working directory, and two workflows cannot bind the same port.

**How to apply:** Make routing and launch changes in validated artifact/workflow configuration only; leave the root Python source untouched.
