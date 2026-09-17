package app.vega.client.security

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Fase 27.2.1 — testes JVM da política FAIL-CLOSED [WanCredentialStore] com
 * armazenamento em memória (sem Android/Keystore). Cobre gravação/leitura
 * normais, migração de plaintext legado, ausência de fallback em claro,
 * decriptação corrompida e releitura após "restart".
 */
class WanCredentialStoreTest {

    /**
     * Dublê do SharedPreferences + Keystore: `blob` = `enc.<key>`, `plain` =
     * valor legado. As falhas são injetáveis.
     */
    private class Harness {
        val blob = HashMap<String, String>()
        val plain = HashMap<String, String>()
        var keystoreAvailable = true
        var encryptThrows = false
        var decryptThrows = false
        var writeCalls = 0
        var legacyRemovedFailClosed = 0

        fun write(value: String?): Boolean = WanCredentialStore.write(
            keystoreAvailable = keystoreAvailable,
            value = value,
            writeEncrypted = { v ->
                writeCalls++
                if (encryptThrows) throw IllegalStateException("keystore write failed")
                blob["enc.x"] = "ENC($v)"
                plain.remove("x")
            },
            removeAll = { blob.remove("enc.x"); plain.remove("x") },
            removeLegacy = { plain.remove("x") },
        )

        fun read(): String? = WanCredentialStore.read(
            keystoreAvailable = keystoreAvailable,
            encryptedBlob = { blob["enc.x"] },
            legacyPlain = { plain["x"] },
            decrypt = { b ->
                if (decryptThrows) throw IllegalStateException("bad blob")
                b.removePrefix("ENC(").removeSuffix(")")
            },
            migrateToEncrypted = { v ->
                if (encryptThrows) throw IllegalStateException("keystore write failed")
                blob["enc.x"] = "ENC($v)"
                plain.remove("x")
            },
            removeLegacy = { plain.remove("x") },
            onLegacyRemovedFailClosed = { legacyRemovedFailClosed++ },
        )
    }

    @Test
    fun writeStoresEncryptedOnly() {
        val h = Harness()
        assertTrue(h.write("token-123"))
        assertEquals("ENC(token-123)", h.blob["enc.x"])
        assertTrue("plaintext nunca é gravado", h.plain.isEmpty())
    }

    @Test
    fun writeBlankRemovesEverything() {
        val h = Harness()
        h.write("token-123")
        assertTrue(h.write(null))
        assertTrue(h.blob.isEmpty() && h.plain.isEmpty())
    }

    @Test
    fun readDecryptsExistingBlob() {
        val h = Harness()
        h.blob["enc.x"] = "ENC(device-9)"
        assertEquals("device-9", h.read())
    }

    @Test
    fun legacyMigratesOnlyWithKeystoreAndRemovesPlaintext() {
        val h = Harness()
        h.plain["x"] = "legacy-token"
        assertEquals("legacy-token", h.read())
        assertEquals("ENC(legacy-token)", h.blob["enc.x"])
        assertTrue("plaintext legado apagado após migração", h.plain.isEmpty())
        assertEquals(0, h.legacyRemovedFailClosed)
    }

    @Test
    fun keystoreDownNeverWritesPlaintext() {
        val h = Harness()
        h.keystoreAvailable = false
        assertFalse(h.write("token-123"))
        assertTrue("nada em claro é persistido", h.plain.isEmpty())
        assertTrue(h.blob.isEmpty())
        assertEquals(0, h.writeCalls)
    }

    @Test
    fun keystoreDownRemovesUnmigratedLegacyPlaintext() {
        val h = Harness()
        h.plain["x"] = "legacy-token"
        h.keystoreAvailable = false
        assertNull(h.read())
        assertTrue("plaintext legado não pode sobreviver", h.plain.isEmpty())
        assertEquals(1, h.legacyRemovedFailClosed)
    }

    @Test
    fun migrationFailureRemovesPlaintextAndFailsClosed() {
        val h = Harness()
        h.plain["x"] = "legacy-token"
        h.encryptThrows = true
        assertNull(h.read())
        assertTrue("plaintext legado é removido se não migrar", h.plain.isEmpty())
        assertEquals(1, h.legacyRemovedFailClosed)
    }

    @Test
    fun corruptBlobNeverReturnsPartialSecret() {
        val h = Harness()
        h.blob["enc.x"] = "lixo-corrompido"
        h.decryptThrows = true
        assertNull(h.read())
    }

    @Test
    fun writeFailureDoesNotLeaveLegacyPlaintext() {
        val h = Harness()
        h.plain["x"] = "legacy-token"
        h.encryptThrows = true
        assertFalse(h.write("novo-token"))
        assertTrue(h.plain.isEmpty())
        assertTrue(h.blob.isEmpty())
    }

    @Test
    fun encryptedCredentialSurvivesRestart() {
        val first = Harness()
        assertTrue(first.write("token-persistente"))

        val restarted = Harness()
        restarted.blob.putAll(first.blob)
        restarted.plain.putAll(first.plain)
        assertEquals("token-persistente", restarted.read())
    }
}