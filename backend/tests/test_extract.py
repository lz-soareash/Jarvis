"""Testes de extração HTML (Fase 14) — conteúdo útil sem scripts/estilos."""

from app.research.extract import extract_html

_SAMPLE = """
<html><head><title>Guia de SSRF</title>
<script>var x=1; // ignora</script>
<style>body{color:red}</style>
</head><body>
<nav>Home | Sobre | Contato</nav>
<article>
  <h1>O que é SSRF</h1>
  <p>Server Side Request Forgery permite explorar a rede interna.</p>
  <p>Prevenção: blocagem de IPs privados e revalidação de redirects.</p>
  <ul><li>Valide esquemas</li><li>Bloqueie metadados</li></ul>
</article>
<footer>© 2026 — ignore</footer>
</body></html>
"""


def test_extract_basic_structure():
    result = extract_html(_SAMPLE, base_url="https://example.com/guia")
    assert result.title == "Guia de SSRF"
    assert any(level == 1 and "SSRF" in text for level, text in result.headings)
    assert "Server Side Request Forgery" in result.text
    assert "explorar a rede interna" in result.text
    assert "javascript" not in result.text.lower(), "script não deve vazar"
    assert "color:red" not in result.text, "style não deve vazar"


def test_extract_skips_navigation_and_scripts():
    result = extract_html(_SAMPLE, base_url="https://example.com/guia")
    assert "Home | Sobre" not in result.text
    assert "© 2026" not in result.text


def test_extract_produces_main_text_and_words():
    result = extract_html(_SAMPLE, base_url="https://example.com/guia")
    assert result.word_count > 5


def test_extract_links_absolute_http_only():
    html = '<a href="/relativa">A</a><a href="https://externo.com/x">B</a><a href="mailto:a@b.c">C</a><a href="javascript:void(0)">D</a>'
    result = extract_html(html, base_url="https://site.com/pag")
    urls = [u for u, _ in result.links]
    assert "https://site.com/relativa" in urls
    assert "https://externo.com/x" in urls
    assert not any("mailto" in u or "javascript" in u for u in urls)


def test_extract_hostile_malformed_html_never_raises():
    result = extract_html("<div><b>texto<br></div> " * 100, base_url="https://x.com")
    assert result is not None
    assert result.word_count >= 0


def test_extract_max_chars_limits_main_text():
    html = "<p>" + "palavra " * 100 + "</p>"
    result = extract_html(html, base_url="https://x.com", max_chars=80)
    assert len(result.main_text) <= 80