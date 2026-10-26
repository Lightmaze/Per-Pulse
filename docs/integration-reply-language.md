# Reply-language preference on the native Pulse + UIUX candidate

This integration ports the **reply-language** half of [the independent Language PR #9](https://github.com/Lightmaze/Per-Pulse/pull/9) onto the [Pulse + UIUX + source-navigation candidate #16](https://github.com/Lightmaze/Per-Pulse/pull/16). It intentionally does not claim to integrate the interface translation catalogues or Posture PR #8. Use this as an intermediate, stacked, reviewable candidate.

## Behavior

Settings → Reply language saves Follow conversation / English / 简体中文 through the existing authenticated `GET/PATCH /api/settings`. The World component state owns the setting; rebooting and switching Work pages does not fork or edit history. The current language is also present in `GET /api/bootstrap`.

The original native Kernel's `LanguageTurnAdvisor` samples the setting at a new turn's first model request, composes with any existing advisor and retains its choice across the turn's tool continuation. An explicit language instruction in the current task overrides the default. The setting is presentation guidance, not a tool permission or a source transformation; changing it does not itself create a model request.

Strict JSON validation rejects unsupported, null, missing and extra fields with 422. Failed state writes roll back the in-memory preference. The dialogue only displays the new value after HTTP confirms it; on network failure, a user can retry without the client assuming it succeeded.

## Boundaries

The interface chrome remains English until the separate UI-catalogue/i18n changes from #9 are properly merged against the Pulse Activity and Core UIUX components. Historical messages, page titles, references, causal edges, posture encounters and drafts remain verbatim. UIUX and Pulse behavior from #16 must not regress.

Regression source: `tests/per_pulse/test_language.py` was carried over from the existing Language branch; tests use native World/Kernel/SQLite and explicit local HTTP fixtures, not remote-provider judgments. CI runs it together with the current integrated suite. Full browser and paid-provider acceptance is still needed.

Follow-up: merge the typed UI catalogue and locale settings (preserving Activity and source-view messages), then Posture #8 and Harness approval/budgets on one candidate; measure actual task completion and fresh-context response quality.
