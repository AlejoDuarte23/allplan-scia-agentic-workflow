import json
import webbrowser
from pathlib import Path
from typing import Any
from collections.abc import Callable


class WorkflowViewer:
    def __init__(
        self,
        workflow_factory: Callable[[], Any],
        *,
        root_dir: Path | None = None,
    ) -> None:
        self._workflow_factory = workflow_factory
        self._root_dir = root_dir or Path().cwd()

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def _model_dump(self, obj: Any) -> dict[str, Any]:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "dict"):
            return obj.dict()
        raise TypeError("Expected a Pydantic model.")

    def render_html(self) -> str:
        # Use the directory where this module is located to find static files
        module_dir = Path(__file__).resolve().parent
        css = (module_dir / "styles.css").read_text(encoding="utf-8")
        js = (module_dir / "workflow.js").read_text(encoding="utf-8")
        js = js.replace("export class WorkflowGraph", "class WorkflowGraph")

        canvas_state = self._workflow_factory()
        state_json = json.dumps(self._model_dump(canvas_state), ensure_ascii=False)

        return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Workflow</title>
    <style>{css}</style>
  </head>
  <body>
    <div class="app">
      <main id="stage">
        <svg id="edges"></svg>
        <div id="nodes"></div>
        <aside id="workflow-overlay" class="workflow-overlay is-hidden"></aside>
        <div class="zoom-controls">
          <button id="zoom-in" title="Zoom In">+</button>
          <button id="zoom-out" title="Zoom Out">−</button>
          <div class="zoom-divider"></div>
          <button id="zoom-fit" title="Fit to View">⊡</button>
          <button id="zoom-reset" title="Reset View">↺</button>
        </div>
      </main>
    </div>

    <script id="workflow-data" type="application/json">{state_json}</script>
    <script>{js}</script>
    <script>
      const dataEl = document.getElementById("workflow-data");
      const canvasState = JSON.parse(dataEl.textContent || "{{}}");

      const graph = new WorkflowGraph({{
        stage: document.getElementById("stage"),
        edgesSvg: document.getElementById("edges"),
        nodesHost: document.getElementById("nodes"),
        overlayEl: document.getElementById("workflow-overlay"),
        logEl: null,
      }});

      graph.setData(canvasState);
      graph.relayout({{ resetDragged: true }});
      graph.render();
      
      // Auto-fit to view on initial load with 1.25x zoom multiplier
      setTimeout(() => graph.fitToView(1.25), 50);

      // Zoom control buttons
      document.getElementById("zoom-in").addEventListener("click", () => graph.zoomIn());
      document.getElementById("zoom-out").addEventListener("click", () => graph.zoomOut());
      document.getElementById("zoom-fit").addEventListener("click", () => graph.fitToView());
      document.getElementById("zoom-reset").addEventListener("click", () => graph.resetView());

      window.addEventListener("resize", () => {{
        graph.relayout({{ resetDragged: false }});
        graph.render();
        graph.fitToView();
      }});
    </script>
  </body>
</html>
"""

    def write(self, out_path: Path | str | None = None) -> str:
        """Generate and return the HTML string without writing to disk."""
        return self.render_html()

    def show(self, out_path: Path | str | None = None) -> Path:
        """Generate HTML and open in browser via temporary file."""
        import tempfile

        html_content = self.render_html()

        if out_path is not None:
            path = Path(out_path)
            path.write_text(html_content, encoding="utf-8")
        else:
            # Create a temporary file
            fd, temp_path = tempfile.mkstemp(suffix=".html", text=True)
            with open(fd, "w", encoding="utf-8") as f:
                f.write(html_content)
            path = Path(temp_path)

        webbrowser.open_new_tab(path.resolve().as_uri())
        return path
