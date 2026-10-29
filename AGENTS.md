# Working on Per Pulse

Build the English-first persistent agent workspace in this repository.

Continue the existing Pulse implementation. Read actual code before integrating it. Reuse the Home/Work and Harness Kernel contracts; do not create a parallel runtime or a second authoritative session store. Preserve source attribution and Apache-2.0 notices.

The first product path is Home conversation, an inline reference to a session page, continued work in its own context, return to the original position, and a selected result with its source. A page is a work context, not automatically a new agent identity.

Keep all product copy, example content, documentation and new tests in English. Keep the primary view quiet and readable. Expose technical details only when they help the user make a decision.

Use real persistence and provider interfaces. Never silently substitute scripted replies for a model. Explicit sample content is acceptable for exploring the interface. Protect user histories and credentials; commit source and synthetic fixtures only.

Use branches, review the diff, and verify the actual user flow in a browser. Record the checks actually run. Ship ordinary working features without introducing speculative research gates.
