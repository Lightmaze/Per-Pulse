# Bring source material into a conversation

Open **Materials**, choose UTF-8 text up to 1 MiB, or a PNG/JPEG image or PDF up to 32 MiB,
and select **Upload file**. A PDF is kept as the original file; its preview and
what Per Pulse reads is its text, with a `[Page N]` line before each page. A
password-protected PDF must be unlocked first. A page with no selectable
text has only its page marker in the text view; the model-reading tools
explicitly report that some pages had no selectable text. PDF reading and
search cover the extracted text, including on pages that mix text with images.
Download the original to inspect images and visual details. A very long PDF is
read up to 2,000 pages or 2 million characters; its text then ends with a line saying where it stops, the
preview says it is incomplete, and the model's reading tools report it
(`pdf_complete` / `source_complete: false`), so a search of the part read is
not taken for a search of the whole PDF. Inspect the preview, then **Add to draft**. Your existing text and page
links stay in place. Tell Per Pulse what you want to do with the material and
send the message when it is ready. Existing files can be reopened from the
same dialog without uploading them again. Ordinary workspace dotfiles, such as
`.env` and `.gitignore`, appear in Materials and can be previewed; the owner's
file contents are shown unchanged. Internal `.pulse` and `.git` directories
and in-progress tool temporary files stay out of the file browser. Uploading
a dotfile keeps its original display name and creates a separately named
copy under `uploads/`, rather than replacing the existing file.

Files belong to the shared native workspace and can be read from any page;
conversation histories remain separate. Each Work keeps its own attachment
record without copying the shared file. A source chip identifies the original
name and version; reopening or sending it checks that the bytes still match.
Open a source chip and choose **Download original** to save its complete uploaded
bytes and original filename, including content beyond the preview. The source
is checked again when requested; a changed file cannot replace the selected
version. Closing its preview cancels a pending download. This also works after
restart with the model disconnected.
Remove the chip to leave the file out of a message. Uploading or previewing does
not run the model, and adding a reference does not mean the model has read the file. With a configured
provider, the existing read tools can open it when working on your request.
Uploads create new files and do not grant model write permissions.

Markdown materials and file results have **Read** and **Source** views. Headings,
lists, tables and code are readable while the original text remains available.
Images embedded in Markdown appear as descriptions; links to external sources open only when chosen.
CSV and TSV files also have a table view. Values remain text, including leading
zeros and empty cells. Use **First row contains headings** to match the file.
The view shows up to 200 data rows and 64 columns, with keyboard scrolling for
wide tables. **Columns** lets you compare a subset without changing the file;
your choices survive switching between Read and Source during that preview.
A partial preview is labelled and omits an unfinished trailing
row; irregular quotes, unequal rows or wider files fall back to source text.
The **Add to draft** action stays visible while reading a long material.

## Read an image

Upload a still PNG or JPEG in **Materials**, inspect its image preview, then choose
**Add to draft** and send your question. Images can contain up to 50 megapixels;
corrupt or animated images are refused with a reason. The original file stays
unchanged, with its exact source version and download.

With an image-compatible model configured, Per Pulse reads the image through
`read_file` and sends it in the next model request: the original when it is at
most 1 MiB and 2048 px on its long edge, otherwise an upright copy scaled to fit,
which the model is told about. The preview shows what the model receives. There is no
separate image-model setting. For compatible models, see the providers'
[OpenAI vision](https://developers.openai.com/api/docs/guides/images-vision) and
[DeepSeek vision](https://api-docs.deepseek.com/guides/vision/) documentation.
Image input is part of normal provider usage. Up to five images can accompany one
request, within the existing request-size bound. If the model needs an image
again in a later turn or after a restart, it reads the source again.

A text-only model refuses image input. If the service refuses a request that
carries an image, Per Pulse sends it once more without the image and tells the
model why, so the reply can say so. Later image reads in the same session are
sent without pixels and with the same note.

## Carry a file result from another Work

Open a finished file result in its producing Work and choose **Add the full
file to another page's draft** or **Add full file to Home draft**. This takes
only the verified current UTF-8 file version and a source-page reference;
it does not send the draft, copy private conversations or authorize an action.
The resulting material now also keeps the **original successful native file
action ID and producing Work**. In the receiving Home or Work composer, select the attached file name **before
sending** to inspect that exact version, the original generating action and the
source Work. Close the preview to continue editing the same unsent draft, or
open the producing Work and return without submitting the draft. The separate
remove button still removes only that attachment. A matching file path, digest or copied text alone
cannot assert which Work created an output. An ordinary file-browser selection
has no generated-action origin unless its original action is explicitly verified.

The backend checks the original settled file-write receipt, its ownership,
exact path, digest and undo state alongside the existing World/file publication
fence. Forged source IDs, a missing half of the source pair, a later file
version or an undone output cannot be imported as a verified generated result.
The existing file reference remains scoped to the receiving page and survives
restart; source identity does not grant more file or execution permissions.
Existing uploaded materials still use their separate original-upload origin.

The native regression runs real World/Kernel/SQLite, file approvals and a
successful workspace write with explicitly synthetic model transport. The
Chromium journey also carries a genuine action receipt into an unsent Home
draft and restores the producing Work's composer. These checks do not measure
real-provider improvement or independent output quality.
