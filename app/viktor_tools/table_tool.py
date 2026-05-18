import viktor as vkt
from pydantic import BaseModel, Field
from typing import Any, Literal


class TableTool(BaseModel):
    """Arguments for a table view tool"""

    data: list[list[str | float | int]] = Field(
        ...,
        description="Table data as a list of rows, where each row is a list of values",
    )
    column_headers: list[str] = Field(..., description=" headers for each column")


class ShowHideTableArgs(BaseModel):
    """Arguments for show/hide table tool"""

    action: Literal["show", "hide"] = Field(
        ...,
        description="Action to perform: 'show' to display the table view, 'hide' to hide it",
    )


async def display_table_func(ctx: Any, args: str) -> str | None:
    """Displays Table in TableView"""

    payload = TableTool.model_validate_json(args)
    print(f"{payload}=")

    if payload:
        vkt.Storage().set(
            "TableTool",
            data=vkt.File.from_data(payload.model_dump_json()),
            scope="entity",
        )
        return "Table generated. Open the Table view panel to view it."
    return f"Validation error Incorrect Outputs {args}"


async def show_hide_table_func(ctx: Any, args: str) -> str:
    """Show or hide the table view."""
    payload = ShowHideTableArgs.model_validate_json(args)
    action = payload.action

    if action == "show":
        print("Showing Table View")
    else:
        print("Hiding Table View")

    vkt.Storage().set(
        "show_table",
        data=vkt.File.from_data(action),
        scope="entity",
    )
    print(f"Table Visibility State Changed to {action}")
    return f"Table Visibility State Changed to {action}"


def generate_table() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="generate_table",
        description=(
            "Generate a table visualization. "
            "Takes data as a list of rows (each row is a list of values). "
            "Accepts column_headers for labeling columns. "
            "The table will be displayed in the Table view panel and can be downloaded as CSV."
        ),
        params_json_schema=TableTool.model_json_schema(),
        on_invoke_tool=display_table_func,
        strict_json_schema=False,
    )


def show_hide_table_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="show_hide_table",
        description=(
            "Show or hide the Table view panel. "
            "Pass 'show' to display the table view, 'hide' to hide it."
        ),
        params_json_schema=ShowHideTableArgs.model_json_schema(),
        on_invoke_tool=show_hide_table_func,
        strict_json_schema=False,
    )
