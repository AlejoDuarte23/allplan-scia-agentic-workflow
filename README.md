# Allplan SCIA Agentic Workflow

VIKTOR app template with:

- `vkt.Chat` powered by the OpenAI Agents SDK
- Workflow graph WebView and plan/progress tools
- VIKTOR API inspection tools for parametrization, defaults, methods, and DataView/TableView outputs
- Table generation tools
- Generic VIKTOR REST API method runner and sibling entity creator
- Monaco workflow code WebView fed through VIKTOR Storage
- Optional OpenAI hosted shell-tool delegation for flexible API exploration

## Local Setup

```bash
viktor-cli install
viktor-cli start
```

Set these environment variables when using the chat agent or VIKTOR API tool:

```bash
OPENAI_API_KEY=...
TOKEN_VK_APP=...
```

`TOKEN_VK_APP` is used by the VIKTOR API tools. `OPENAI_API_KEY` is also required when using the hosted shell-tool workflow.
