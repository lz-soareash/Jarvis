package app.vega.client.mobile

/**
 * Fase 27.2 (F2) — segunda defesa de OPEN_URL no executor (Kotlin PURO,
 * testável em JVM). O Core já valida http(s) antes de despachar; aqui a URL
 * é revalidadapara que mesmo um móvel comprometido não abra esquemas
 * perigosos via `Intent.ACTION_VIEW`.
 *
 * Regras (espelham `mobile_capabilities.validate_http_url` do backend):
 *  - apenas `http://`/`https://` (rejeita `intent`, `file`, `content`,
 *    `javascript`, `tel`, `mailto`, custom e ausência de scheme);
 *  - credenciais embutidas (`user:pass@host`) rejeitadas;
 *  - authority (host[:porta]) obrigatória, sem whitespace/controle.
 *  - vazia/em branco rejeitada.
 */
object UrlAllowlist {
    private val allowedSchemes = setOf("http", "https")

    /** Devolve a mensagem de erro ou `null` quando a URL é segura. */
    fun validate(url: String): String? {
        val raw = url.trim()
        if (raw.isEmpty()) return "URL vazia"
        if (raw.any { it.isWhitespace() || it.code < 0x20 }) return "URL com caracteres inválidos"
        val schemeEnd = raw.indexOf("://")
        if (schemeEnd <= 0) return "URL sem scheme (apenas http/https)"
        val scheme = raw.substring(0, schemeEnd).trim().lowercase()
        if (scheme !in allowedSchemes) return "scheme '$scheme' não permitido (apenas http/https)"
        val authority = raw
            .substring(schemeEnd + 3)
            .substringBefore('/')
            .substringBefore('?')
            .substringBefore('#')
            .trim()
        if (authority.isEmpty()) return "host ausente"
        if ('@' in authority) return "credenciais embutidas não são permitidas"
        return null
    }
}