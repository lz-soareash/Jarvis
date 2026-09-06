"""Proactive Agent (Fase 17) — proatividade controlada do JARVIS.

Pacote com o ciclo EVENTS → POLICY → DECISION → DELIVERY, sempre com default
conservador (`PROACTIVE_ENABLED=false`). Reutiliza infraestrutura existente
(broker pub/sub, rate limiter, auditoria, permissões, observabilidade) —
não duplica eventos de domínio nem abre caminhos de escape de permissão.
"""