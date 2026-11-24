# Return from a nested result source

A result brought to Home can originate in a Work created inside another Work.
Opening its source and choosing **Back to conversation** must return to the
Home message that was opened, even when the source Work's parent is different.

The conversation source link now supplies its current page and message to the
existing navigation history contract. The original target message, scroll
position and separate unsent drafts continue through the same route handling.
No page parent, persisted message or source attribution is changed.

The existing `tests/browser/result_reading_flow.mjs` journey now creates a
parent Work and a nested result, brings a selected passage to Home, opens its
source, returns to the exact Home message, checks both drafts and opens the
original result again. The old client failed by returning to the parent Work;
the fixed client passes this journey and its existing desktop, phone, exact
copy, inert content and clipboard-denial checks. The model is not involved:
the fixture is explicitly authored interface content with native persistence.
