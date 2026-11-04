# Bring source material into a conversation

Open **Materials**, choose a UTF-8 text file up to 1 MiB, and select **Upload
file**. Inspect the preview, then **Add to draft**. Your existing text and page
links stay in place. Tell Per Pulse what you want to do with the material and
send the message when it is ready. Existing files can be reopened from the
same dialog without uploading them again.

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
Images appear as descriptions; links to external sources open only when chosen.
CSV and TSV files also have a table view. Values remain text, including leading
zeros and empty cells. Use **First row contains headings** to match the file.
The view shows up to 200 data rows and 64 columns, with keyboard scrolling for
wide tables. **Columns** lets you compare a subset without changing the file;
your choices survive switching between Read and Source during that preview.
A partial preview is labelled and omits an unfinished trailing
row; irregular quotes, unequal rows or wider files fall back to source text.
The **Add to draft** action stays visible while reading a long material.

## Carry a file result from another Work

Open a finished file result in its producing Work and choose **Add the full
file to another page's draft** or **Add full file to Home draft**. This takes
only the verified current UTF-8 file version and a source-page reference;
it does not send the draft, copy private conversations or authorize an action.
The resulting material now also keeps the **original successful native file
action ID and producing Work**. Open its source chip to see the generating Work
and return to that page. A matching file path, digest or copied text alone
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
