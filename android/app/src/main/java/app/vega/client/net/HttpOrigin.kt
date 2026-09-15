package app.vega.client.net

/**
 * Fase 27.1 — derivação PURA da origem HTTP do Core (Kotlin puro, testável sem
 * Android/OkHttp).
 *
 * O endpoint WAN é sempre `ws://`/`wss://` (validado por `WanEndpoint`), mas as
 * chamadas HTTP (`/api/...`) exigem `http://`/`https://`. Normalizar essa base
 * aqui evita o crash do OkHttp "Expected URL scheme 'http' or 'https' but no
 * scheme was found" quando a base está vazia ou sem esquema.
 */
object HttpOrigin {

    /**
     * Normaliza uma base para origem HTTP (`scheme://host[:porta]`), descartando
     * path/query/fragmento. Aceita:
     *  - `http(s)://host...` (mantém);
     *  - `ws(s)://host...` (ws→http, wss→https);
     *  - `host[:porta][/path]` (assume http);
     *  - vazio/em branco ou esquema desconhecido → `null`.
     */
    fun normalize(base: String?): String? {
        val raw = base?.trim().orEmpty()
        if (raw.isEmpty()) return null

        val withScheme = when {
            raw.startsWith("http://", ignoreCase = true) ||
                raw.startsWith("https://", ignoreCase = true) -> raw
            raw.startsWith("ws://", ignoreCase = true) -> "http://" + raw.substring(5)
            raw.startsWith("wss://", ignoreCase = true) -> "https://" + raw.substring(6)
            "://" in raw -> return null
            else -> "http://$raw"
        }

        val sep = withScheme.indexOf("://")
        val scheme = withScheme.substring(0, sep).lowercase()
        if (scheme != "http" && scheme != "https") return null

        val authority = withScheme
            .substring(sep + 3)
            .substringBefore('/')
            .substringBefore('?')
            .substringBefore('#')
            .trim()
        if (authority.isEmpty()) return null
        if (authority.any { it.isWhitespace() }) return null

        return "$scheme://$authority"
    }

    /**
     * Origem HTTP derivada de um endpoint WAN (`ws`→`http`, `wss`→`https`).
     * Devolve `null` quando a WAN não está configurada ou não é ws(s).
     */
    fun fromWan(wanUrl: String?): String? {
        val raw = wanUrl?.trim().orEmpty()
        if (!raw.startsWith("ws://", ignoreCase = true) &&
            !raw.startsWith("wss://", ignoreCase = true)
        ) {
            return null
        }
        return normalize(raw)
    }
}
