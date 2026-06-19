# Microsoft Word Reference Manager Integration

This integration provides a Zotero/Mendeley-like reference manager for Microsoft Word on macOS that connects directly to your Local Literature Library. It allows you to search your library, insert locked, multi-select in-text citations, and dynamically compile your bibliography in various CSL styles (Nature, IEEE, NAR, NLM, ACS).

---

## 1. How It Works (Architecture)

1. **Office JS Web Add-in Framework**: The integration is built using the modern Office JavaScript API. The task pane inside Word loads a sandboxed React app.
2. **Local Secure Server (HTTPS)**: Microsoft Word sandboxes web add-ins and blocks any non-secure (HTTP) content. The local FastAPI server (`run_webapp.py`) automatically runs over SSL/HTTPS on `https://127.0.0.1:8000` using local self-signed certificates (`cert.pem`/`key.pem`).
3. **Citations & Bibliography Controls**: 
   * Citations are inserted into Word as **Content Controls** tagged as `lib-cite:paper_id_1,paper_id_2`.
   * The bibliography is inserted as a Content Control tagged as `lib-bibliography`.
   * To prevent manual formatting accidents (which often break other reference managers like Mendeley), all content controls are locked by default (`cannotEdit = true`). They can still be deleted or moved around.
4. **Selected Items Pinning**: Any paper you check/select is pinned to the very top of the list in the task pane. It remains checked and visible even when you change search queries, preventing search resets from clearing or hiding your selections.
5. **Safe Lock Lifecycle & Layout Stabilization**: To avoid Word's `GeneralException` or layout lock errors when inserting/refreshing citations:
   * Document formatting runs in three separate phases: **Unlock** all target controls -> **Update** text/superscripts -> **Relock** controls.
   * On citation/bibliography insertion, the cursor selection is automatically shifted **after** the newly created control to prevent layout collisions.
   * A brief **200ms stabilization delay** is executed between insertion and auto-refresh to let Word's layout engine settle.
6. **Batch Compilation (Pandoc)**: When you click **Refresh All**, the add-in scans the document for citation controls in order, sends their IDs and paper references to `/api/citations/format`, runs `pandoc --citeproc` in a single pass to format number sequences, ranges, and alphabetical bibliography listings, and updates Word in-place.
7. **Superscript Formatting**: The add-in automatically parses superscript markdown markers from Pandoc (like `^(1,2)` or unicode superscripts like `¹`) and translates them into Word's native superscript format (`font.superscript = true`) while inserting clean numbers.

---

## 2. Prerequisites

Ensure you have the following installed on your Mac:
* **Pandoc**: Required for formatting bibliography styles via CSL. Install it via Homebrew:
  ```bash
  brew install pandoc
  ```
* **Python 3**: With `fastapi`, `uvicorn`, and `Pillow` (Pillow is used to draw the PNG icons for the Word ribbon).
* **Node.js & npm**: For compiling the React frontend.

---

## 3. Step-by-Step Setup

### Step 1: Sideload the Manifest
Run the sideload helper script. This copies the add-in's `manifest.xml` directly into Microsoft Word's secure Developer Add-ins container folder:
```bash
python3 scripts/sideload_word_plugin.py
```

### Step 2: Establish Certificate Trust (Crucial)
Because the local server runs on a self-signed HTTPS certificate, macOS will block Word from loading the page until the certificate is trusted system-wide.

**Run this terminal command to trust the certificate instantly (recommended)**:
```bash
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain /Users/brandani/Dropbox/documents/library/cert.pem
```
*(Alternatively, you can open Keychain Access, search for `localhost`, double-click the certificate, expand "Trust", and change the setting to "Always Trust".)*

### Step 3: Compile the Frontend Assets
Run the build script inside the `web-app` folder to bundle the Word task pane entry point (`word.html`):
```bash
cd web-app
npm run build
cd ..
```

### Step 4: Start the Web App
Launch the backend server:
```bash
python3 scripts/run_webapp.py
```
*(The server will start up in HTTPS mode: `Enabling HTTPS (SSL)...` and listen on `https://localhost:8000`)*

---

## 4. How to Load and Use in Microsoft Word

1. **Quit Word Completely**: Close all Word documents and press **Cmd + Q** (or right-click the Word icon in the Dock and select **Quit**). Word will not detect new sideloaded add-ins unless it is freshly started.
2. **Open Word** and create a new document or open an existing one.
3. **Open Add-ins Manager**:
   * Click the **Add-ins** button on the far right of the **Home** ribbon tab, and select **More Add-ins** (or go to **Insert** -> **My Add-ins**).
4. **Add the Library Plugin**:
   * Click the **Developer Add-ins** tab at the top of the dialog box.
   * You should see **Local Literature Library** listed. Select it and click **Add** (if it doesn't show up, click the **Refresh** circular arrows in the top-right corner of the dialog).
5. **Open Task Pane**:
   * A new tab **Literature Library** will appear on the Word Ribbon. 
   * Click **Insert Citations** to open the side pane.
6. **Citing and Formatting**:
   * Use the search bar to find articles.
   * Check the boxes next to one or more articles.
   * **Selected Pinning**: Checked papers are dynamically pinned to the top of the search list. You can enter new queries and search again without losing sight of what is currently selected.
   * Place your cursor in the document and click **Insert Citation**.
   * Go to the end of your document, click **Add Bibliography** to insert the bibliography.
   * Change the **Citation Style** dropdown (e.g. from *Nature* to *IEEE*) to watch the document citations and bibliography update dynamically.
   * Click **Refresh All** after writing or moving text around to recalculate numbers and ranges.

---

## 5. Troubleshooting

* **Add-ins button is missing from the Ribbon**:
  Your Office privacy settings may have connected experiences disabled. Go to **Word -> Preferences -> Privacy** and ensure **Enable optional connected experiences** is checked. Restart Word.
* **Side pane shows a "Blocked / Security Certificate Not Valid" error**:
  This happens if you haven't trusted the self-signed certificate. Make sure you run the `sudo security add-trusted-cert ...` command in Step 2, and click **Restart** inside the Word error panel.
* **"GeneralException" or lock errors when inserting/refreshing citations**:
  Word can raise this if layout updates collision. The plugin includes selection shifting (cursor moves outside the control on insert), a 200ms layout delay, and a 3-phase lock management cycle (Unlock -> Update -> Lock) to prevent this. If you encounter a transient lock error, simply click the **Refresh All** button in the task pane to force a clean, document-wide sync.
* **CSL formatting inconsistencies (e.g. NAR author initials missing)**:
  If a bibliography style renders names or abbreviations incorrectly, check the CSL templates in `paper_index/csl/`. The local `nar.csl` template has been audited and repaired to correctly output full names with initials (like `Brandani G, Takada S.`) instead of omitting them.
