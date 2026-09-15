package app.vega.client

/**
 * Validação centralizada do endpoint WAN (relé) do VEGA Mobile.
 *
 * Bugfix "LAN/WAN separation": a WAN NUNCA é derivada da LAN e NUNCA aceita um
 * endereço privado como endpoint público. A LAN continua livre para usar
 * `http://<IP privado>:8100`; esta validação se aplica APENAS ao endpoint WAN.
 *
 * Regras:
 * - Host obrigatório e endpoint não vazio.
 * - Esquema: `wss://` na produção (`requireTls=true`). Em debug
 *   (`requireTls=false`) `ws://` também é aceito — apenas para testes contra um
 *   relé PÚBLICO (ex.: Quick Tunnel). `http://`/`https://` sempre rejeitados.
 * - Rejeita localhost / 127.0.0.1 / 0.0.0.0 / ::1.
 * - Rejeita endereços privados RFC1918 como endpoint WAN: 10.x.x.x,
 *   172.16–31.x.x e 192.168.x.x (válidos para LAN, nunca para WAN).
 * - Caminho compatível com `/api/remote/ws` (vazio ou "/" também aceitos).
 * - Sem credenciais embutidas na URL (`user@host`).
 */
object WanEndpoint {

    const val WAN_WS_PATH: String = "/api/remote/ws"

    data class Result(
        val valid: Boolean,
        val normalized: String,
        val reason: String? = null,
    ) {
        companion object {
            fun ok(url: String) = Result(true, url)
            fun invalid(reason: String) = Result(false, "", reason)
        }
    }

    /** Valida (e normaliza) um endpoint WAN. `requireTls=false` é apenas para
     *  build de debug/testes contra relé público. */
    fun validate(raw: String?, requireTls: Boolean = true): Result {
        val t = raw?.trim().orEmpty()
        if (t.isEmpty()) return Result.invalid("URL do gateway WAN não configurada")

        val schemeEnd = t.indexOf("://")
        if (schemeEnd <= 0) return Result.invalid("use wss://<endpoint-público>/api/remote/ws")
        val scheme = t.substring(0, schemeEnd).lowercase()
        val rest = t.substring(schemeEnd + 3)

        val authorityEnd = rest.indexOfFirst { it == '/' || it == '?' || it == '#' }
        val authority = if (authorityEnd < 0) rest else rest.substring(0, authorityEnd)
        val pathRaw = if (authorityEnd < 0) "" else rest.substring(authorityEnd)

        if (authority.isBlank()) return Result.invalid("host do gateway WAN vazio")

        when (scheme) {
            "wss" -> Unit
            "ws" -> if (requireTls) return Result.invalid("WAN de produção deve usar wss:// (TLS)")
            else -> return Result.invalid("WAN deve usar wss:// (TLS) — não use http:// ou https://")
        }

        val hostResult = validateAuthority(authority)
        if (!hostResult.valid) return hostResult

        val path = pathRaw.substringBefore('?').substringBefore('#')
        if (!pathCompatible(path)) {
            return Result.invalid("caminho do gateway WAN deve ser compatível com $WAN_WS_PATH")
        }

        val normalized = (scheme + "://" + authority + path).trimEnd('/').takeIf { it.isNotEmpty() } ?: (scheme + "://" + authority)
        return Result.ok(normalized)
    }

    fun isValid(raw: String?, requireTls: Boolean = true): Boolean = validate(raw, requireTls).valid

    /** Normalização para campos pré-configurados (ex.: lembrar URL em runtime). */
    fun normalizePreset(raw: String?, requireTls: Boolean = true): String {
        val r = validate(raw, requireTls)
        return if (r.valid) r.normalized else ""
    }

    private fun pathCompatible(path: String): Boolean {
        if (path.isBlank() || path == "/") return true
        return path.trimEnd('/').endsWith(WAN_WS_PATH)
    }

    private fun validateAuthority(authority: String): Result {
        if (authority.contains(' ') || authority.contains('\t')) {
            return Result.invalid("host do gateway WAN inválido")
        }
        if (authority.indexOf('@') >= 0) {
            return Result.invalid("não use credenciais na URL do gateway")
        }
        val host = hostOf(authority)
        if (host.isBlank()) return Result.invalid("host do gateway WAN vazio")
        return validateHost(host)
    }

    private fun hostOf(authority: String): String {
        // IPv6 literal: [::1]:8200 → dentro dos colchetes.
        if (authority.startsWith("[")) {
            val close = authority.indexOf(']')
            return if (close > 1) authority.substring(1, close) else authority
        }
        // IPv4/hostname com porta: separa no último ':' (hostname pode ter ':'? não).
        val lastColon = authority.lastIndexOf(':')
        if (lastColon > 0 && authority.indexOf(':') == lastColon && authority.substring(lastColon + 1).toIntOrNull() != null) {
            return authority.substring(0, lastColon)
        }
        return authority.substringBefore(':')
    }

    private fun validateHost(host: String): Result {
        val h = host.lowercase().trim()
        if (h.isEmpty()) return Result.invalid("host do gateway WAN vazio")
        if (h == "localhost") return Result.invalid("localhost não é um endpoint WAN público")
        if (h == "0.0.0.0" || h == "::" || isLoopbackLiteral(h)) {
            return Result.invalid("endereço de loopback/local não é um endpoint WAN público")
        }
        if (isIpv6Literal(h)) {
            if (h.startsWith("fc") || h.startsWith("fd") || h.startsWith("fe80")) {
                return Result.invalid("endereço privado não pode ser endpoint WAN")
            }
            return Result.ok("wss://$h")
        }
        if (isIpv4(h)) {
            if (isPrivateIpv4(h)) {
                return Result.invalid("IP privado não pode ser endpoint WAN (use um endpoint público)")
            }
            if (h.startsWith("127.")) return Result.invalid("endereço de loopback não é um endpoint WAN público")
            return Result.ok("wss://$h")
        }
        // Hostname: aceito (DNS público normal); WSS exige TLS de qualquer forma.
        if (!h.all { it.isLetterOrDigit() || it == '.' || it == '-' || it == '_' }) {
            return Result.invalid("host do gateway WAN inválido")
        }
        return Result.ok("wss://$h")
    }

    private fun isIpv4(host: String): Boolean {
        val octets = host.split('.')
        if (octets.size != 4) return false
        return octets.all { o -> o.toIntOrNull()?.let { it in 0..255 } == true }
    }

    /** RFC1918 + metas/reservados comuns que nunca devem ser endpoint WAN. */
    private fun isPrivateIpv4(host: String): Boolean {
        val octets = host.split('.').map { it.toIntOrNull() ?: 0 }
        if (octets[0] == 10) return true
        if (octets[0] == 172 && octets[1] in 16..31) return true
        if (octets[0] == 192 && octets[1] == 168) return true
        if (octets[0] == 169 && octets[1] == 254) return true // link-local
        if (octets[0] == 0 || octets[0] == 255) return true
        if (octets[0] == 127) return true // loopback (rejeita 127.*)
        if (host.startsWith("100.") || host.startsWith("198.18.") || host.startsWith("198.19.")) return true
        return false
    }

    private fun isLoopbackLiteral(host: String): Boolean =
        host == "::1" || host == "0:0:0:0:0:0:0:1"

    private fun isIpv6Literal(host: String): Boolean = host.contains(':')
}