# Getting started with zinegen (Claude Code or Codex)

From nothing to your first print-ready photobook PDF in about 30 minutes. Most of the time goes to the Adobe
credential; the rest is one command each.

You need:

- A Mac or Linux machine. Windows works through WSL.
- An Adobe account with a Lightroom plan (the cloud Lightroom, not only Lightroom Classic) and at least one
  album you want to turn into a book.
- A Claude Code or Codex subscription. Either agent drives the whole process; you only do the browser steps.

## The fastest path: let the agent do it

Open Claude Code or Codex in an empty folder and paste this:

```
Set up zinegen for me by following https://github.com/longfin/zinegen/blob/main/docs/getting-started.md
step by step. Run every command yourself; stop and tell me exactly what to do when a step is marked [human].
When `zinegen doctor` prints "Setup is complete.", list my Lightroom albums and ask which one to typeset.
```

The agent installs the tools, runs the checks, and hands you the two browser steps (Adobe credential, sign-in).
The rest of this page is what it will follow; read on if you prefer doing it by hand.

## 1. Install the system tools

macOS:

```bash
brew install uv pango ghostscript
```

Debian/Ubuntu:

```bash
sudo apt install -y libpango-1.0-0 libpangoft2-1.0-0 ghostscript fontconfig
curl -LsSf https://astral.sh/uv/install.sh | sh
```

`uv` installs Python itself if needed. `pango` is WeasyPrint's text engine; `ghostscript` renders the proofs.

## 2. Install lr-mcp and create the Adobe credential [human]

zinegen reads photos through [lr-mcp](https://github.com/longfin/lr-mcp), an MCP server for the Lightroom
cloud API. Adobe requires each user to create their own API credential (the integration is limited to 25 beta
users per credential, so none is shared).

```bash
uv tool install lr-mcp
lr-mcp doctor
```

`doctor` prints the first thing to fix. Follow the lr-mcp README sections **1. Create an Adobe credential**,
**2. Save the credential** and **3. Sign in** exactly. In short:

1. [human] In the [Adobe Developer Console](https://developer.adobe.com/console) create a project, add the
   **Lightroom Services** API with an **OAuth Web App** credential, set the redirect URI to
   `https://localhost:8000/callback` and the pattern to `https://localhost:8000/.*`, then add your own Adobe
   email under **Manage beta users**.
2. Put the Client ID and Client Secret into `~/.config/lr-mcp/.env` with an editor (never paste the secret into
   the chat):
   ```bash
   mkdir -p ~/.config/lr-mcp
   [ -f ~/.config/lr-mcp/.env ] || printf 'ADOBE_CLIENT_ID=\nADOBE_CLIENT_SECRET=\n' > ~/.config/lr-mcp/.env
   chmod 600 ~/.config/lr-mcp/.env
   ```
3. [human] `lr-mcp login` opens the browser; sign in and click through the self-signed-certificate warning for
   `localhost`.

`lr-mcp doctor` must end with `Catalog found. Setup is complete.`

## 3. Install zinegen

```bash
uv tool install zinegen
zinegen doctor
```

Every line should start with `[ok]`. If the ICC line says none were found, download a press profile
(for example ISO Coated v2 from [eci.org](https://www.eci.org/)) and later put its path in `output.icc` of your
`book.yaml`. On a Mac with Creative Cloud ever installed, Japan Color 2001 Coated and FOGRA39 are found
automatically.

## 4. Register the MCP servers and the skill

Claude Code. The plugin brings the skill and the `zinegen` MCP server together; only `lightroom` is added by hand:

```bash
claude mcp add lightroom -s user -- "$(which lr-mcp)"
```

then install the plugin (the repository is also a marketplace named `longfin`):

```bash
claude plugin marketplace add longfin/zinegen     # the marketplace is named `longfin`
claude plugin install zinegen@longfin
```

Codex:

```bash
codex mcp add lightroom -- "$(which lr-mcp)"
codex mcp add zinegen   -- "$(which zinegen)" mcp
npx skills add longfin/zinegen -a codex
```

`npx skills add longfin/zinegen` without `-a` offers every agent it knows (Claude Code, Codex, Cursor, ...).
If you would rather not run either installer, clone https://github.com/longfin/zinegen and copy the folder by hand:

```bash
mkdir -p ~/.claude/skills && cp -r skills/zinegen ~/.claude/skills/zinegen   # Claude Code
mkdir -p ~/.codex/skills  && cp -r skills/zinegen ~/.codex/skills/zinegen    # Codex
```

Start a new agent session afterwards. `lr_*` and `zg_*` tools should appear in the tool list.

## 5. Make the first book

In the agent, say something like:

```
Make an A5 perfect-bound photobook from my Lightroom album "Kyoto 2020" for a Korean offset printer
(PDF/X-1a, Japan Color 2001 Coated). Use the album's order, write short captions I can edit later,
and show me the spreads before you finish.
```

What happens, and what you will see:

1. The agent lists albums (`zg_list_albums`), then drafts `books/<album>/book.yaml` (`zg_init_book`).
2. It edits the YAML: page sequence, a template per page, captions, title and colophon text.
3. `zg_fetch` downloads full-size renditions (a few seconds per photo the first time; cached afterwards).
4. `zg_render` writes `books/<album>/out/interior.pdf`; `zg_proof` renders spread images that the agent looks
   at and critiques, usually two or three rounds.
5. `zg_check` must come back `ok: true`. The agent reports warnings such as photos below 300 dpi or images that
   cross the gutter.

Open `books/<album>/proof/spread-*.png` to review, edit `book.yaml` yourself if you like, and ask the agent to
re-render. The PDF in `out/` is what you send to the printer, together with the ICC profile name.

Useful follow-ups:

- "Switch to PDF/X-4 for Blurb" (sets `output.standard: pdf/x-4`; RGB images are kept as-is).
- "Make it 20 × 20 cm" (`trim: {width_mm: 200, height_mm: 200}`).
- "What spine width for 110 µm paper?" (`zg_spine`).

## Without an agent

The same steps as plain commands:

```bash
zinegen albums kyoto
zinegen init --album <album id> --dir books/kyoto
$EDITOR books/kyoto/book.yaml
zinegen build books/kyoto          # fetch + render + proof + check
```

`zinegen presets` lists trim presets and binding rules; the `zg_spec_help` MCP tool (or `docs/03-schemas.md`)
documents every `book.yaml` key.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `zinegen doctor`: WeasyPrint cannot load pango | `brew install pango` (macOS) or the apt packages above. On macOS zinegen adds `/opt/homebrew/lib` to the library path itself. |
| `doctor`: Lightroom: Not signed in / Token refresh failed | `lr-mcp login` again, then `lr-mcp doctor`. |
| `doctor`: CMYK ICC profiles found: none | Download a press profile and set `output.icc: /path/to/profile.icc` in `book.yaml`. PDF/X-4 also needs one for the output intent. |
| Tools missing in the agent | Start a new session; check `claude mcp list` or `codex mcp list`; the command must be an absolute path. The plugin runs `zinegen mcp` by name, so `which zinegen` must succeed in the shell that starts Claude Code (`uv tool update-shell` if it does not). |
| `zg_fetch` is slow | Full-size renditions are generated on Adobe's side on first request. The cache makes later runs instant. |
| `zg_check` reports dpi below 300 | Use a smaller template (`margin` instead of `full-bleed`) or a higher-resolution photo. Below 200 the check fails. |
| Spread image warning about the gutter | Perfect binding hides 6–8 mm at the spine. Set `focal` to move the subject off-centre or use `layflat`/`saddle` binding. |
| Korean text shows as boxes | Install an OFL Korean font (Nanum, Noto Sans KR, Pretendard) and set it in `fonts:`. |
