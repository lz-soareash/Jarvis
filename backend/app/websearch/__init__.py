"""Esqueleto abstrato de busca na web (Fase 13 — reservado p/ fase futura Web).

Esta fase NÃO executa chamadas de rede. O contrato existe para que a futura
fase "Web Research" integre provedores reais (DuckDuckGo, Google CSE, Brave...)
sem trocar o consumidor. Regras:

- Node: o `agent` decide; este pacote só define o contrato do provider.
- Local-first: chamadas reais ficam opt-in (config), nunca default nos testes.
"""