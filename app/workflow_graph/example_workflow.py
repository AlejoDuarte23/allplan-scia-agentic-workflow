from app.workflow_graph.models import Connection, Node, Workflow
from app.workflow_graph.state import build_canvas_state


def example_workflow() -> Workflow:
    return Workflow(
        nodes=[
            Node(
                id="allplan_model",
                title="Prepare Allplan Model",
                type="allplan_model",
            ),
            Node(
                id="scia_model",
                title="Create SCIA Model",
                type="scia_model",
                depends_on=[Connection(node_id="allplan_model")],
            ),
            Node(
                id="run_viktor_checks",
                title="Run VIKTOR Checks",
                type="viktor_api_tool",
                depends_on=[Connection(node_id="scia_model")],
            ),
            Node(
                id="results_table",
                title="Review Results Table",
                type="table_output",
                depends_on=[Connection(node_id="run_viktor_checks")],
            ),
        ]
    )


if __name__ == "__main__":
    from app.workflow_graph.viewer import WorkflowViewer

    WorkflowViewer(lambda: build_canvas_state("Allplan SCIA Workflow", example_workflow())).show()
