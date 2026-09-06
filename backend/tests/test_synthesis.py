"""Testes de síntese com citações (Fase 14) — fontes reais, nunca inventadas."""

import json

from app.research.evidence import Evidence
from app.research.synthesis import (
    build_synthesis_messages,
    deterministic_synthesis,
    parse_synthesis_answer,
)

EVIDENCE = [
    Evidence(source_url="https://a.com/1", title="Fonte A", excerpt="Paris é a capital da França."),
    Evidence(source_url="https://b.com/2", title="Fonte B", excerpt="A Torre Eiffel fica em Paris."),
]


def test_build_messages_separates_content():
    system, messages = build_synthesis_messages("Qual a capital?", EVIDENCE)
    assert "precedência" in system.lower()
    # O aviso sobre conteúdo não-confiável vive nas INSTRUÇÕES (system), não no conteúdo.
    assert "não confiável" in system.lower() or "não-confiável" in system.lower()
    user = messages[0].content
    assert "<web-content>" in user and "</web-content>" in user
    assert "[1] https://a.com/1" in user
    assert "[2] https://b.com/2" in user


def test_parse_drops_fabricated_citations_and_unsourced_facts():
    reply = {
        "answer": "A capital é Paris (fonte [1]).",
        "facts": [
            {"text": "Paris é a capital.", "sources": [1]},  # ok
            {"text": "A capital do Brasil é Brasília.", "sources": [99]},  # inventado→drop
            {"text": "Fato sem fonte."},  # sem fonte→drop
        ],
        "inferences": [{"text": "Provavelmente levou ~séculos de história.", "rationale": "inferência"}],
        "uncertainties": [{"text": "População exata não confirmada."}],
        "citations": [
            {"source": 1, "excerpt": "Paris é a capital da França."},
            {"source": 99, "excerpt": "fonte que não existe"},  # inventado→drop
        ],
    }
    parsed = parse_synthesis_answer(json.dumps(reply), EVIDENCE)
    assert parsed["answer"] == "A capital é Paris (fonte [1])."
    assert len(parsed["facts"]) == 1
    assert parsed["facts"][0]["sources"] == [1]
    assert len(parsed["citations"]) == 1
    assert parsed["citations"][0]["source_url"] == "https://a.com/1"


def test_parse_handles_malformed_json_and_fences():
    parsed = parse_synthesis_answer("```json\n{\"answer\": \"sim\", \"facts\": []}\n```", EVIDENCE)
    assert parsed["answer"] == "sim"
    parsed = parse_synthesis_answer("texto livre sem json", EVIDENCE)
    assert parsed["answer"] == ""


def test_deterministic_synthesis_only_real_evidence():
    outcome = deterministic_synthesis("Qual a capital?", EVIDENCE)
    urls = [c["source_url"] for c in outcome["citations"]]
    assert set(urls) == {"https://a.com/1", "https://b.com/2"}
    assert all(u.startswith("https://") for u in urls)


def test_deterministic_synthesis_empty_evidence():
    outcome = deterministic_synthesis("X", [])
    assert outcome["answer"]
    assert outcome["citations"] == []