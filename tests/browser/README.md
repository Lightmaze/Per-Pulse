# Browser flow check

This checks the built React interface against the actual local World and
Harness Kernel. The HTTP model transport is an explicit synthetic fixture;
every generated reply and stored experience is marked accordingly. It neither
contacts an external provider nor establishes learned model capability.

Build the client and install the Python development dependencies using the
main README. Install Playwright in a separate tooling directory, then run:

```sh
npm install --prefix /tmp/per-pulse-browser-tools playwright@1.62.1
node /tmp/per-pulse-browser-tools/node_modules/playwright/cli.js install chromium
PLAYWRIGHT_MODULE=/tmp/per-pulse-browser-tools/node_modules/playwright \
  node tests/browser/posture_flow.mjs
```

The script starts its own loopback server on an unused port and uses a fresh
temporary data directory, which is removed afterward. `PER_PULSE_PYTHON` can
select another Python environment; it defaults to `.venv/bin/python`.
`PLAYWRIGHT_CHROMIUM_EXECUTABLE` can select an installed Chromium binary.
`PLAYWRIGHT_CHROMIUM_ARGS` accepts an optional JSON array of launch arguments.
`PER_PULSE_SCREENSHOTS` selects an optional output directory for desktop and
mobile screenshots. Test data and images should stay outside the repository.

The flow sends an ordinary task followed by a correction, checks the resulting
lesson after refreshing a panel that was open during background learning,
checks its exact source, pauses it and restarts the service, resumes it, shares it with
a new page, verifies that the original encounter stays at the source, returns
to that source, resolves a revision conflict between two browser tabs, and
records harmful feedback. It also checks the mobile dialog,
keyboard dismissal, and browser runtime errors.
