from __future__ import annotations

import json

import viktor as vkt

CODE_STORAGE_KEY = "viktor_workflow_code_files"
CODE_VISIBILITY_KEY = "show_viktor_code_editor"


def save_code_files(files: dict[str, str], *, show: bool = True) -> None:
    if not files:
        files = {"workflow.py": "# No code generated yet\n"}
    vkt.Storage().set(
        CODE_STORAGE_KEY,
        data=vkt.File.from_data(json.dumps(files)),
        scope="entity",
    )
    if show:
        set_code_editor_visibility("show")


def load_code_files() -> dict[str, str]:
    try:
        raw = (
            vkt.Storage()
            .get(CODE_STORAGE_KEY, scope="entity")
            .getvalue_binary()
            .decode("utf-8")
        )
        payload = json.loads(raw)
        if isinstance(payload, dict) and all(isinstance(k, str) for k in payload):
            return {str(k): str(v) for k, v in payload.items()}
    except Exception:
        pass
    return {
        "workflow.py": (
            "# The agent will save generated VIKTOR workflow code here.\n"
            "# Ask it to inspect two apps and generate the bridge script.\n"
        )
    }


def set_code_editor_visibility(action: str) -> None:
    if action not in {"show", "hide"}:
        raise ValueError("Code editor action must be 'show' or 'hide'.")
    vkt.Storage().set(
        CODE_VISIBILITY_KEY,
        data=vkt.File.from_data(action),
        scope="entity",
    )


def get_code_editor_visibility(params, **kwargs) -> bool:
    if not params.chat:
        try:
            vkt.Storage().delete(CODE_VISIBILITY_KEY, scope="entity")
        except Exception:
            pass
        return False

    try:
        action = vkt.Storage().get(CODE_VISIBILITY_KEY, scope="entity").getvalue()
        return action == "show"
    except Exception:
        return False


def render_code_editor_html() -> str:
    files_json = json.dumps(load_code_files())
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Workflow Code</title>
    <style>
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        background: #0d1117;
        color: #c9d1d9;
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }}
      #code-panel {{
        display: grid;
        grid-template-columns: 280px minmax(0, 1fr);
        height: 100vh;
        overflow: hidden;
      }}
      #file-list {{
        border-right: 1px solid #30363d;
        background: #010409;
        padding: 10px;
        overflow: auto;
      }}
      #file-list-title {{
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #8b949e;
        padding: 6px 10px 10px;
      }}
      .file-button {{
        width: 100%;
        border: 0;
        background: transparent;
        color: #8b949e;
        text-align: left;
        padding: 9px 10px;
        border-radius: 8px;
        cursor: pointer;
        font: inherit;
        font-size: 13px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        display: flex;
        align-items: center;
        gap: 7px;
      }}
      .file-button:hover {{
        background: #161b22;
        color: #f0f6fc;
      }}
      .file-button.active {{
        background: #1f6feb;
        color: #fff;
      }}
      #main {{
        min-width: 0;
        display: grid;
        grid-template-rows: 44px minmax(0, 1fr);
      }}
      #current-file {{
        display: flex;
        align-items: center;
        padding: 0 14px;
        border-bottom: 1px solid #30363d;
        background: #0d1117;
        color: #f0f6fc;
        font-size: 14px;
        font-weight: 600;
      }}
      #editor {{ min-height: 0; }}
    </style>
  </head>
  <body>
    <section id="code-panel">
      <aside id="file-list">
        <div id="file-list-title">Explorer</div>
      </aside>
      <main id="main">
        <header id="current-file"></header>
        <div id="editor"></div>
      </main>
    </section>
    <script src="https://cdn.jsdelivr.net/npm/monaco-editor@0.55.1/min/vs/loader.js"></script>
    <script>
      const initialFiles = {files_json};
      require.config({{
        paths: {{ vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.55.1/min/vs" }}
      }});
      require(["vs/editor/editor.main"], function () {{
        const fileListEl = document.getElementById("file-list");
        const currentFileEl = document.getElementById("current-file");
        const editorEl = document.getElementById("editor");
        let files = {{ ...initialFiles }};
        let activePath = Object.keys(files)[0] || "";
        const models = new Map();
        const editor = monaco.editor.create(editorEl, {{
          theme: "vs-dark",
          readOnly: true,
          automaticLayout: true,
          minimap: {{ enabled: false }},
          fontSize: 14,
          lineHeight: 22,
          scrollBeyondLastLine: false,
          wordWrap: "on"
        }});
        function createUri(path) {{
          return monaco.Uri.parse("file:///" + path.replaceAll("\\\\", "/"));
        }}
        function languageFor(path) {{
          if (path.endsWith(".json")) return "json";
          if (path.endsWith(".md")) return "markdown";
          if (path.endsWith(".py")) return "python";
          return "plaintext";
        }}
        function getOrCreateModel(path) {{
          if (models.has(path)) return models.get(path);
          const model = monaco.editor.createModel(
            files[path] || "",
            languageFor(path),
            createUri(path)
          );
          models.set(path, model);
          return model;
        }}
        function renderFileList() {{
          const title = fileListEl.querySelector("#file-list-title");
          fileListEl.innerHTML = "";
          fileListEl.appendChild(title);
          Object.keys(files).sort().forEach(path => {{
            const btn = document.createElement("button");
            btn.className = "file-button" + (path === activePath ? " active" : "");
            btn.title = path;
            btn.innerHTML =
              `<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor">
                <path d="M3 1h6l4 4v10H3V1z" opacity=".35"></path>
                <path d="M9 1v4h4"></path>
              </svg>` +
              `<span>${{path}}</span>`;
            btn.addEventListener("click", () => openFile(path));
            fileListEl.appendChild(btn);
          }});
        }}
        function openFile(path) {{
          if (!(path in files)) return;
          activePath = path;
          currentFileEl.textContent = path;
          editor.setModel(getOrCreateModel(path));
          renderFileList();
        }}
        window.agentCodePanel = {{
          openFile,
          setFiles(nextFiles) {{
            for (const model of models.values()) model.dispose();
            models.clear();
            files = {{ ...nextFiles }};
            activePath = Object.keys(files)[0] || "";
            renderFileList();
            if (activePath) openFile(activePath);
            else {{ currentFileEl.textContent = "No files"; editor.setModel(null); }}
          }},
          getFiles() {{
            const result = {{}};
            for (const path of Object.keys(files)) {{
              const model = models.get(path);
              result[path] = model ? model.getValue() : files[path];
            }}
            return result;
          }}
        }};
        renderFileList();
        if (activePath) openFile(activePath);
      }});
    </script>
  </body>
</html>"""
