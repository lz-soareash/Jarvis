package app.vega.client.security

import java.security.SecureRandom
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * Fase 27.2 (F1) — criptografia AES/GCM das credenciais WAN (Kotlin PURO,
 * testável em JVM, sem dependências Android).
 *
 * O key material vem do Android Keystore via [WanSecretStore]; aqui vive apenas
 * o formato do blob:
 *
 *   "v1." + Base64Url( iv[12] || ciphertext || gcm_tag[16] )
 *
 * AES/GCM é autenticado: blob corrompido/revirado/sem integridade falha na
 * verificação do tag e NUNCA decripta parcialmente (sem oráculo de padding).
 */
object WanCiphers {
    const val VERSION_PREFIX = "v1."
    private const val ALGO = "AES/GCM/NoPadding"
    private const val TAG_BITS = 128
    private val ivSize = 12

    fun encrypt(key: SecretKey, plain: String): String {
        require(plain.isNotEmpty()) { "valor vazio não pode ser criptografado" }
        val iv = ByteArray(ivSize).also { SecureRandom().nextBytes(it) }
        val cipher = Cipher.getInstance(ALGO)
        cipher.init(Cipher.ENCRYPT_MODE, key, GCMParameterSpec(TAG_BITS, iv))
        val out = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        val payload = ByteArray(iv.size + out.size)
        System.arraycopy(iv, 0, payload, 0, iv.size)
        System.arraycopy(out, 0, payload, iv.size, out.size)
        return VERSION_PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(payload)
    }

    fun decrypt(key: SecretKey, blob: String): String {
        require(blob.startsWith(VERSION_PREFIX)) { "formato do blob desconhecido" }
        val payload = Base64.getUrlDecoder().decode(blob.substring(VERSION_PREFIX.length))
        require(payload.size > ivSize) { "blob curto demais" }
        val iv = payload.copyOfRange(0, ivSize)
        val ct = payload.copyOfRange(ivSize, payload.size)
        val cipher = Cipher.getInstance(ALGO)
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(TAG_BITS, iv))
        return String(cipher.doFinal(ct), Charsets.UTF_8)
    }
}