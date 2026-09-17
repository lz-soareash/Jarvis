package app.vega.client.security

/**
 * FASE 27.2.1 — política FAIL-CLOSED de persistência de credencial WAN.
 *
 * Camada pura (Kotlin, testável em JVM — sem Android Keystore) que decide SOB
 * QUAIS CONDIÇÕES uma credencial é lida/persistida. A criptografia em si vive
 * em [WanCiphers] (AES-256-GCM) com a chave do [WanSecretStore]; aqui vive
 * apenas a política:
 *
 * - Keystore disponível → credencial gravada SOMENTE como blob AES-256-GCM;
 * - Keystore indisponível/falha de escrita → NOTHING é gravado em claro e
 *   qualquer plaintext legado é removido (fail-closed);
 * - decriptação falha → nunca devolve segredo parcial/corrompido (nulo);
 * - migração de plaintext legado só acontece com Keystore disponível; se não
 *   for possível, o plaintext é REMOVIDO e a leitura devolve nulo (re-pareia).
 */
object WanCredentialStore {

    /**
     * Lê uma credencial. Nunca devolve um segredo que não esteja seguro:
     *
     * - blob AES-256-GCM válido → valor decriptado;
     * - legado em claro migrado com sucesso → valor (blob gravado + plaintext
     *   apagado);
     * - decriptação falhou → `null` (nunca parcial/corrompido);
     * - legado sem Keystore / falha de migração → `null` e `removeLegacy()`
     *   apaga o plaintext (fail-closed; `onLegacyRemovedFailClosed` é
     *   notificado para log/erro controlado).
     */
    fun read(
        keystoreAvailable: Boolean,
        encryptedBlob: () -> String?,
        legacyPlain: () -> String?,
        decrypt: (String) -> String,
        migrateToEncrypted: (String) -> Unit,
        removeLegacy: () -> Unit,
        onLegacyRemovedFailClosed: () -> Unit = {},
    ): String? {
        val blob = encryptedBlob()
        if (!blob.isNullOrBlank()) {
            return try {
                decrypt(blob)
            } catch (e: Throwable) {
                null
            }
        }
        val legacy = legacyPlain()
        if (legacy.isNullOrBlank()) return null
        if (!keystoreAvailable) {
            removeLegacy()
            onLegacyRemovedFailClosed()
            return null
        }
        return try {
            migrateToEncrypted(legacy)
            legacy
        } catch (e: Throwable) {
            removeLegacy()
            onLegacyRemovedFailClosed()
            null
        }
    }

    /**
     * Persiste uma credencial. TRUE apenas quando o segredo foi gravado como
     * blob AES-256-GCM. Com Keystore indisponível ou falha de escrita retorna
     * FALSE e NUNCA grava plaintext (remove qualquer plaintext legado).
     * Valor nulo/em branco → remove as chaves e retorna TRUE.
     */
    fun write(
        keystoreAvailable: Boolean,
        value: String?,
        writeEncrypted: (String) -> Unit,
        removeAll: () -> Unit,
        removeLegacy: () -> Unit,
    ): Boolean {
        if (value.isNullOrBlank()) {
            removeAll()
            return true
        }
        if (!keystoreAvailable) {
            removeLegacy()
            return false
        }
        return try {
            writeEncrypted(value)
            true
        } catch (e: Throwable) {
            removeLegacy()
            false
        }
    }
}