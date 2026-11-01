# selene-space-ext-ollama

SeleneQuant extension that registers a local Ollama LLM provider using `selene_space_llm.ollama.OllamaProvider`.

## Defaults

- Base URL: `http://localhost:11434` when `LLMConfig.base_url` is unset.

## Install

From the repository root (development):

```bash
pip install -e ./extensions/ollama
```

## Entry point

- `selene_space_ext_ollama.plugin:setup` — calls `register_llm_provider("ollama", OllamaExtensionProvider)`.
