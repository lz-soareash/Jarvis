package app.vega.client.security

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

/**
 * Fase 27.2 (F1) — testes JVM da camada pura [WanCiphers] com chave em memória
 * (sem Android). Cobre round-trip, não-determinismo (IV aleatório), formato de
 * saída, integridade (fail-closed) garantida pelo tag GCM e entradas inválidas.
 */
class WanCiphersTest {
    private val key: SecretKey = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()

    @Test
    fun encryptDecryptRoundTrip() {
        val blob = WanCiphers.encrypt(key, "segredo-abc123")
        assertTrue(blob.startsWith(WanCiphers.VERSION_PREFIX))
        assertEquals("segredo-abc123", WanCiphers.decrypt(key, blob))
    }

    @Test
    fun ciphertextIsNonDeterministic() {
        val a = WanCiphers.encrypt(key, "mesmo-valor")
        val b = WanCiphers.encrypt(key, "mesmo-valor")
        assertNotEquals(a, b)
    }

    @Test
    fun emptyBlobFormatIsRejected() {
        val e = assertThrows(IllegalArgumentException::class.java) {
            WanCiphers.decrypt(key, WanCiphers.VERSION_PREFIX)
        }
        assertTrue(e.message!!.contains("curto demais"))
    }

    @Test
    fun wrongKeyFailsClosedNeverPartial() {
        val blob = WanCiphers.encrypt(key, "senha-certa")
        val other = KeyGenerator.getInstance("AES").apply { init(256) }.generateKey()
        assertThrows(Exception::class.java) { WanCiphers.decrypt(other, blob) }
    }

    @Test
    fun tamperedBlobFailsClosed() {
        val blob = WanCiphers.encrypt(key, "integridade")
        val tamperedBase = blob.substring(WanCiphers.VERSION_PREFIX.length)
        val tail = if (tamperedBase.endsWith('=')) tamperedBase.dropLast(1) else tamperedBase
        val tampered = WanCiphers.VERSION_PREFIX + tail + "A"
        assertThrows(Exception::class.java) { WanCiphers.decrypt(key, tampered) }
    }

    @Test
    fun unknownVersionPrefixRejected() {
        assertThrows(IllegalArgumentException::class.java) { WanCiphers.decrypt(key, "v0." + "AAAA") }
    }

    @Test
    fun emptyPlainRejected() {
        assertThrows(IllegalArgumentException::class.java) { WanCiphers.encrypt(key, "") }
    }
}