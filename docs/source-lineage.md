# Source lineage

Per Pulse continues the maintainer's existing Pulse implementation. The initial English workspace was integrated from source snapshot `20208de1fef6bd946cf1cad62e96782e6a9a0dc0`; it was not reconstructed from a product description or a runtime summary.

The original public Pulse baseline is [Lightmaze/pulse](https://github.com/Lightmaze/pulse/tree/59841fa11484825b997aae6bd9ad91da9b3c78be), licensed under Apache-2.0. This repository preserves that license and its NOTICE. The import includes runtime source, not the original repository's history or personal runtime state.

## Integration boundary

- `src/pulse_system/` and `src/pulse_harness/` preserve the native World, Home/Work, Kernel, and provider implementation.
- `src/per_pulse/` adds the English workspace's HTTP projection and local entry point.
- `web/` implements the English session-page client.
- `src/pulse_system/service/kernel_runtime.py` adds an explicit `allow_unconfigured` option for a disconnected workspace. The option defaults to false. Without it, existing provider checks retain their behavior. Disconnected mode does not run the model loop or generate fallback replies.

Home and Work retain one continuity subject. A session page is a view over a native Work, with its own context. References and source anchors are lightweight metadata in the same World database. No parallel conversation database or replacement turn runner is introduced.

The inherited source includes modules beyond this first interface's scope. Shipping a module does not mean its complete UI, provider behavior, or research claims have been verified by this release. The new product entry point and focused tests define the supported initial path.

## Private state stays outside source

The source distribution excludes credentials, private configuration, databases, event logs, live Pulse Images, and personal workspaces. New users start with their own data directory. The explicit example workspace contains newly authored English sample notes and always runs without a model.

The Python and browser checks for this integration validate software behavior. They do not constitute a real-provider benchmark or evidence of improved cognition.
