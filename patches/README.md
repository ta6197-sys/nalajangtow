# Safe existing-file integration

This package deliberately does **not** ship full replacements for `web_app.py`, `compose.yaml`, or `web/index.html` because they contain existing user work and the running legacy web.

Instead `tools/apply_integration.py` generates and applies **small deterministic guarded modifications**. Run `--check` to view the unified diff first; run `--apply` only after reviewing. If anchors differ, it fails without writing a partial patch.

Affected existing files:

1. `web_app.py`: static V5 page and API routes, read-only GET and small isolated POST execution.
2. `compose.yaml`: loopback-only host port, V5 preview config and persistent result mount.
3. `web/index.html`: one link to the V5 preview page.

No existing V5 engine Python file is replaced.
