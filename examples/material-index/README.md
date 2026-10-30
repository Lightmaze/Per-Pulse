# Build a reading index from material you actually used

This small, runnable work product turns a UTF-8 JSON material list into an
offline HTML index. It needs only Python's standard library. It does not fetch
addresses, run a model, or start another Per Pulse runtime.

From the repository root:

```sh
python examples/material-index/build_index.py \
  examples/material-index/materials.json /tmp/reading-materials.html
```

Open the resulting file in a browser. Supply your own list of objects with
`title`, `url`, and `note` strings; `url` and `note` are optional. `--title`
changes the document title. The output must be a new file, so a second run
cannot silently overwrite an earlier result.

Titles and notes stay literal, including markup, quotes, combining characters,
and multilingual text. Eligible HTTP(S) addresses become links; other entries
remain readable text. The generator rejects credentials, control characters,
whitespace, backslashes, parser-rejected hosts, and invalid ports from link targets.
It does not assess the trustworthiness or availability of a destination.

## The experiment that changed this work

Initial hypothesis: HTML-escaping both a title and its URL is enough to make
an imported material a suitable clickable link.

The actual reading sources were Python 3.12's
[html.escape documentation](https://docs.python.org/3.12/library/html.html#html.escape)
and [URL parsing security](https://docs.python.org/3.12/library/urllib.parse.html#url-parsing-security).
The first describes character conversion; the second requires application
judgment about URL components. Neither gives a destination a trust assessment.

A local Chromium counterexample used `html.escape(..., quote=True)` to build a
link whose address was `javascript:document.body.dataset.executed=1`. Clicking
it really set the marker to `1`. This refuted the hypothesis: escaping an HTML
attribute preserves a URL's executable scheme. The minimal counterexample
deliberately had no content security policy, isolating the escaping assumption.

The finished generator separates two decisions: escape all displayed strings,
and choose link destinations independently. Its output also has a restrictive
content security policy. Actual browser inspection of the generated sample
found two material links, no injected image/script elements, and no horizontal
overflow at 390 pixels. Rejected addresses do not discard the associated title
or note. A second input situation preserves a Unicode field note and a query
containing both ampersands and quotes without manufacturing extra attributes.

## Use the lesson in Per Pulse

1. Put a short attributed excerpt of the official source in a workspace material.
   Read it with native `reading`; keep an exact observed substring with
   `posture.remember_reading`. The interpretation starts as a local note.
2. Run a counterexample, then record the result in the reading Work. Use
   `posture.revise` with that actual feedback message and the current revision
   to correct the note. Keep the original excerpt and source unchanged.
3. Explicitly choose **Use in other pages** in the experience dialog. In another
   Work, retrieve that example and read a concrete material list. Apply the
   revised interpretation to the generator's output and approve the file action.
4. Open and download the recorded HTML artifact, inspect it in a browser, and
   **Bring to Home** the selected result. Its source opens the exact Work result;
   the reading example's source separately returns to the original reading turn.

This path was actually exercised on the main `8abbe175` source tree, then
repeated after main `d2b10081` integrated the condition editor, with the
generator in this directory and explicit host-mediated `TEST_DOUBLE` choices.
Native reading, journal revision, denial before sharing, explicit sharing,
approved file writing, exact artifact download, Home attribution, source return,
both Work drafts, and restart used the existing World/Kernel/SQLite. Desktop
English and a Chinese 390px phone view completed with no page errors. The
approved HTML included the corrected note and had SHA-256
`18bb5e36d1652e914ec61d6d1cfe1f116fed5686616efce8dac465645b4f0489`.
The combined candidate also passed 28 native/example checks, 62 frontend checks,
the production build, and the existing condition-editing browser journey.

The operator supplied the interpretation and renderer; this is a developer's
completed learning/practice cycle and a native product-path check. It is not
evidence that a model independently discovered the correction or improved its
learning quality. The provider check returned `disconnected`, recorded as
`blocked_provider_unconfigured`. The synthetic World and its browser captures
remain local; only source and synthetic input are published.

```sh
python -m pytest -q tests/per_pulse/test_material_index_example.py
```

These three checks cover active/ambiguous URL input, literal hostile markup,
multilingual text and query preservation, and invalid material structure.
The example is also included in the source distribution. An actual extracted
sdist initially failed test collection because the program was omitted while
its test was included; adding `/examples` to the existing sdist include list
corrected that packaging error. The extracted package's checks and CLI run were
then verified directly.
