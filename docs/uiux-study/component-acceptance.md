# Per Pulse interaction acceptance plan

Prepared during the research-only phase, 2026-10-23. These are planned checks; no product behavior is claimed verified yet.

## Composer and linking

| Case | Action | Observable result |
|---|---|---|
| Home scope | Focus the Home composer | Accessible name identifies Home; placeholder offers a natural continuation |
| Page scope | Open Reading room and focus composer | Accessible name identifies Reading room; same composer mechanics as Home |
| Disconnected | Inspect composer and submit a short message | Connection limitation is visible; saved content is confirmed honestly; no fabricated assistant response |
| Existing draft | Type a sentence, open page picker, cancel | Sentence remains byte-for-byte intact; composer regains focus |
| Useful default | Open Link a page without typing | Existing pages appear in stable order; current page exclusion follows product semantics |
| Keyboard selection | Type a partial title, ArrowDown/Up, Enter | Active row is visible; correct page reference inserts once; message is not sent |
| No match | Type an impossible title | Clear no-match text; Escape and close remain available |
| Composition | Send synthetic composing Enter, then normal Enter | Composition confirmation does not send; normal Enter sends once |
| Newline | Shift+Enter in draft | Inserts a newline; no message is sent |
| Touch | Use picker and close at 390px width | Options and controls have readable labels and reachable hit targets |

## Create and Bring to Home

| Case | Action | Observable result |
|---|---|---|
| Clear creation | Open New page from an existing message | Purpose and source are visible; name field has a label; primary action is clear |
| Empty title | Leave name blank | Creation remains unavailable without a valid title |
| Cancel | Edit fields, then Escape or Cancel | No page/content mutation; focus returns to the trigger |
| Transfer preview | Open Bring to Home from Reading room | Source, destination, and editable content are visible before commit |
| Edited transfer | Replace preview text and confirm | Home receives the edited text and a source link; the original conversation remains available |
| Empty transfer | Clear preview | No meaningless blank content can be created |
| Keyboard-only | Tab through any dialog and reverse | Focus stays within visible dialog and returns logically after close |
| Narrow dialog | Open at 390×844 and short viewport | Header, fields, and actions remain reachable without horizontal overflow |

## Parent integration checks

- Navigating Home→session→Home retains the Home reading position.
- Session drafts remain attached to their session when navigation remounts the composer.
- Bringing content Home is distinct from merely returning Home.
- The returned source link opens the same session object; it does not create a new one.
- The app uses actual ProviderState capabilities to distinguish save, connection, and work status.
- Dialog success may focus the newly created page/content if that is the next logical workflow step; dismissal returns to the trigger.
