# Per Pulse web

The English interface for the Per Pulse workspace. This React, TypeScript, and Vite application uses the existing Python workspace API as the sole authority for conversations, pages, and references.

## Run

Start the Python API on `127.0.0.1:8000` using the repository instructions, then:

```sh
npm ci
npm run dev
```

Vite binds only to `127.0.0.1` and proxies `/api` to the local Python application. Do **not** expose this development server to your network: the proxy provides access to an unauthenticated, single-user workspace API. The preview server also binds to loopback. For a production build:

```sh
npm run build
```

The Python application serves `web/dist`, including direct links to `/page/{id}`. No external font, image, or analytics services are required.

## Interaction

- Open an inline page reference to continue its own conversation.
- Use **Back to conversation** or the breadcrumbs to return. Reading positions and unsent drafts are kept separately for each page.
- Create a page from the sidebar or a message. The source conversation receives a reference to the same persistent page.
- Use **Link a page** in the composer to insert a readable page name with its stable reference. Existing references never duplicate a page or start model work.
- Link an existing page to a message using its contextual action.
- Select text in a Work message and choose **Bring to Home**, or bring the complete message. The editable return preserves a link to its source.
- Select a Work title to rename it. Search is also available with `Cmd/Ctrl + K`.

Server errors are shown without substituting a demo or invented reply. When AI is disconnected, messages are still saved and visibly queued. Example workspaces are explicitly labeled; their authored content is separate from live model output.

## Frontend state

`api.ts` and `types.ts` define the API boundary. `useWorkspace.ts` handles page loading, real work polling, and browser navigation. Components in `src/components` own the sidebar, conversation, composer, links, and dialogs.

Browser storage uses the `per-pulse.ui.v2` prefix, scoped to the server's stable workspace ID, and contains only unsent drafts and reading positions. Example and ordinary workspaces never share drafts, even on the same origin. Unscoped older drafts are not imported. Server histories are not duplicated into browser storage. Requests in progress cannot clear a newer draft.

## Checks

`npm run build` runs strict TypeScript checking and creates the production bundle. Browser interaction and visual verification are recorded in the repository handoff.
