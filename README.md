# AI Website Builder

A PyQt6 desktop agent that builds websites in a real project folder, with a live PHP preview on the right and an OpenAI-compatible chat on the left.

The agent can read and edit files, search the project, drive the preview in a real browser engine, inspect PHP server logs, search DuckDuckGo, and take full-page desktop + phone screenshots for vision models.

```
┌─────────────────────────────┬──────────────────────────────────────┐
│  chat · streaming · thinking│  address bar  ← → ⟳ ↗               │
│                             │                                      │
│  tool cards + screenshots   │        live PHP preview             │
│                             │        (QWebEngineView)             │
│  input + reference images   │                                      │
└─────────────────────────────┴──────────────────────────────────────┘
```

## Features

- **OpenAI-compatible chat** — configurable base URL, API key, model, context length and timeout. Any `/v1/chat/completions` server works (OpenAI, OpenRouter, DeepSeek, LM Studio, Ollama, vLLM, …).
- **Streaming** responses with a live **thinking / reasoning** panel (`reasoning_content`, `reasoning`, inline think blocks).
- **Multimodal input** — attach, paste or drag reference images into the chat. Images are saved in the project and sent as vision content.
- **Tool calling** — a full agent loop with multiple tools and multiple tool rounds per turn.
- **Project per website** — every project has a public `web/` document root. A PHP development server starts on a random free port and the preview opens automatically.
- **Live QWebEngine preview** with an editable address bar, history navigation, reload and open-in-browser.
- **Agent-only screenshots**:
  - `screenshot` captures the **entire page** (not just the visible viewport) for desktop and for a real 390×844 phone viewport. Both modes are returned by default, so the model can check responsiveness.
  - The phone image is rendered in a hidden, off-screen browser view; the main window layout is never disturbed.
  - `screenslice` captures a readable close-up around any CSS selector (± 300 px by default).
- **Browser tools** — load a URL/path and get JS console errors plus screenshots; click elements by selector; fill inputs/textareas/selects/contenteditable fields and optionally submit their form.
- **Server log tool** — the agent can inspect PHP request logs, warnings and fatal errors.
- **DuckDuckGo web search** — rendered in a hidden off-screen browser (DuckDuckGo blocks plain HTTP scrapers from many networks). The window is never shown. The Instant Answer API fallback is still available to the non-GUI registry.
- **Chat history per project** — `chat_history.json` stores user, assistant, reasoning, tool calls, tool results, images and compaction markers.
- **Automatic context compaction** — when the estimated context reaches the configured percentage, older messages are summarised into a handoff summary and kept in the history file. The UI shows a compaction marker that expands to the summary.
- **Local and private** — only your configured API endpoint, DuckDuckGo (for web search) and any URLs you load in the preview receive traffic. Settings live in `~/.config/AIWebSiteBuilder/settings.json`; the API key is stored there in plain text.

## Requirements

- Python 3.10+
- PHP 8+ on `PATH` (for the preview server)
- A running X11/Wayland desktop session (QWebEngine cannot render without a display server)
- Python packages from `requirements.txt`:
  - `PyQt6`
  - `PyQt6-WebEngine`
  - `requests`

On minimal Linux distributions, Qt WebEngine also needs the usual Chromium runtime libraries (for example `libnss3`, `libxcomposite1`, `libxdamage1`, `libxrandr2`, `libasound2`, `libxtst6`). The `PyQt6-WebEngine` wheel bundles Qt WebEngine itself.

## Install

```bash
git clone git@github.com:tizbac/aiwebsitebuilder.git
cd aiwebsitebuilder
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python3 main.py
# or open a specific project directly:
python3 main.py --project /path/to/project
```

On first launch:

1. Open **File → Settings…** (`Ctrl+,`) and configure:
   - API base URL, e.g. `https://api.openai.com/v1`
   - API key (leave empty for local servers)
   - Model
   - Context length (used for the usage meter and compaction)
   - Request timeout
   - Optional: compaction threshold, max tool rounds, image max side
2. **File → New Project…** (`Ctrl+N`), choose a name, parent folder and starter template.
3. The app creates the folder, starts PHP, and opens the preview. Describe the site and press **Enter**.

The base URL is normalised: if it already ends in `/v1`, `/chat/completions` is appended; otherwise `/v1/chat/completions` is appended.

## Project layout

```
my-site/
├── web/                     # public document root served by PHP
│   ├── index.php
│   └── assets/…
├── chat_history.json        # complete conversation + compaction markers
├── project.json             # project metadata
├── .aibuilder/
│   ├── attachments/         # reference images uploaded in chat
│   └── screenshots/         # full-page and slice screenshots
├── README.md
└── .gitignore
```

The PHP server runs from the project root with `-t web`, so only `web/` is public. The agent is sandboxed to the project root; paths outside it are rejected.

## Tools available to the model

| Tool | Purpose |
| --- | --- |
| `list_files` | Inspect the project tree. |
| `read_file` | Read a UTF-8 file, optionally by line range. Output includes line numbers. |
| `write_file` | Create a file or fully replace it. |
| `edit_file` | Exact `old_string` → `new_string` replacement. Requires a unique match unless `replace_all` is true. |
| `grep` | Regex content search with file paths and line numbers, optional context and glob filters. |
| `search` | Find files/folders by substring or glob. |
| `screenshot` | Full-page desktop and phone screenshots (both by default). Images are returned to the model. |
| `screenslice` | Full-width vertical slice around a CSS selector, element ± padding (default 300 px). |
| `webview_load` | Load a URL or project path, return JS console errors/warnings, page title and screenshots. |
| `click` | Click the first element matching a CSS selector; returns the resulting URL and new JS errors. |
| `fill` | Fill an input/textarea/select/contenteditable by selector and dispatch `input`/`change`; optionally submit the form. |
| `server_log` | Read recent PHP server output, with an optional filter. |
| `web_search` | Search DuckDuckGo and return titles, URLs and snippets. |

Every tool result is text; screenshots and reference images are attached to the next model turn as images, so any vision-capable model can inspect them.

## Screenshots

- Desktop screenshots scroll the visible preview from top to bottom and stitch the strips at full page height.
- Phone screenshots load the current preview URL in a hidden 390×844 `QWebEngineView` (using the same browser profile) and stitch the full page there. The hidden view is created with `WA_DontShowOnScreen`, so no extra window ever appears.
- Both are scaled down to **Settings → Image max side** (default 1568 px) before being sent.
- Sticky/fixed elements can repeat at strip boundaries on very long pages; this is a limitation of stitched screenshots.
- `screenslice` keeps a close-up at readable resolution by cropping the stitched full-page image around the selected element.

## Chat and compaction

- Streaming text and reasoning deltas are rendered live; reasoning is shown in a collapsible panel and stored in history.
- The context meter uses the API's reported usage where available and a heuristic estimate otherwise.
- When usage approaches the configured threshold (default 80%), the agent asks the model for a detailed handoff summary of older messages, stores the summary, and inserts a compaction marker. The full transcript is never deleted from `chat_history.json`.
- Summarisation can be chunked for small context windows. If compaction fails, the error is shown and the current turn continues without blocking.
- **Chat → Compact Context** triggers compaction manually. **Chat → Clear Chat** deletes the history file contents.

## Web search

`web_search` runs `https://duckduckgo.com/?q=…&ia=web` inside a hidden `QWebEngineView` and extracts the rendered result list with JavaScript. This uses DuckDuckGo's public web results without an API key and avoids the 202 anti-bot page that plain HTTP requests often receive. The hidden browser is created with `WA_DontShowOnScreen` and is never visible. `app/tools/websearch.py` also contains a pure-`requests` implementation (HTML + Instant Answer API) used when no GUI handler is attached.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Ctrl+N` | New project |
| `Ctrl+O` | Open project |
| `Ctrl+,` | Settings |
| `F5` | Reload preview |
| `Ctrl+Q` | Quit |
| `Esc` | Stop the agent |
| `Enter` | Send message |
| `Shift+Enter` | New line in the message box |
| `Ctrl+V` | Paste an image from the clipboard as an attachment |

## Troubleshooting

- **“PHP: offline”** — install PHP or ensure it is on `PATH`; check **Project → Show Server Log**.
- **Blank preview / renderer crash** — try `QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu" python3 main.py`. When running as root in a container, add `--no-sandbox`. On some Wayland setups, `QT_QPA_PLATFORM=xcb` may help.
- **API errors** — verify the endpoint, key and model. The status bar and chat tool cards show the server's error message.
- **Context full** — lower the context length in Settings to the model's real limit, or use **Chat → Compact Context**.
- **DuckDuckGo returns nothing** — it may be blocked by a network policy; the agent can still use `webview_load` on documentation URLs.

## License

Copyright (C) 2026 **Tiziano Bacocco**.

This project is free software distributed under the **GNU General Public License, version 3 or later** (GPLv3). See [LICENSE](LICENSE) for the full text.

## Source layout

```
main.py                      entry point / QApplication setup
app/config.py                settings dataclass + persistence
app/project.py               project folder, scaffolding, path sandbox
app/php_server.py            PHP dev server + line-buffered log capture
app/capture.py               full-page desktop/phone/slice capture
app/utils.py                 image scaling, data URIs, formatting
app/chat/
  client.py                  streaming OpenAI-compatible client + reasoning parser
  agent.py                   tool loop, history, compaction, stop/close handling
  compaction.py              boundary selection, summarisation workers
  history.py                 chat_history.json persistence
  prompts.py                 system + summarisation prompts
  tokens.py                  context estimates
app/tools/
  files.py                   read/write/edit/grep/search/list tools
  registry.py                tool schemas, dispatch, background worker
  websearch.py               DuckDuckGo requests fallback
app/ui/
  main_window.ui/.py         main window and wiring
  settings_dialog.ui/.py     endpoint, context, timeout, advanced settings
  new_project_dialog.ui/.py  project creation
  log_dialog.ui/.py          PHP/server log viewer
  chat_view.py               message cards, streaming rendering, images
  webview.py                 QWebEngineView with JS console capture
  search_view.py             hidden DuckDuckGo browser + result extraction
  style.qss                  dark theme
```
