"""Web Search (Fase 14) — provedores de busca intercambiáveis, local-first.

A coleta aqui é independente de LLM e de credenciais (DuckDuckGo hoje). O
consumidor é o pacote `app.research` (Research Agent), e as tools
`web_search`/`web_fetch` do Agentic Core. A validação de segurança (SSRF) da
navegação de páginas vive em `app.research.ssrf` e é aplicada no Fetcher.
"""