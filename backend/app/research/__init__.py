"""Web Research & Knowledge Integration (Fase 14).

Camada local-first de pesquisa: Busca (app.websearch) → Coleta SSRF-segura
(fetcher) → Extração (extract) → Evidências (evidence) → Síntese com citações
(synthesis) → Research Agent (agent.py) → Candidatos a conhecimento e escrita
no Atlas (knowledge). Nada aqui toca rede desprotegida; conteúdo web é sempre
dado não-confiável.
"""