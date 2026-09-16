package app.vega.client.security

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey

/**
 * Fase 27.2 (F1) — fonte do key material no ANDROID KEYSTORE (alias fixo
 * `vega_wan_key`). A chave AES é gerada e mantida pelo Keystore: material
 * criptográfico nunca sai do TEE/StrongBox, nunca é serializado em
 * SharedPreferences e é destruído em factory-reset/desinstalação (fail-closed:
 * blob indecifrável → usuário reapareia).
 */
object WanSecretStore {
    private const val ALIAS = "vega_wan_key"

    /** Gera (primeira vez) ou recupera a chave AES/GCM do Keystore. */
    fun key(context: Context): SecretKey {
        val ks = keystore()
        if (ks.containsAlias(ALIAS)) {
            (ks.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.secretKey?.let { return it }
        }
        val kg = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        kg.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .build()
        )
        return kg.generateKey()
    }

    /** TRUE quando o Android Keystore está disponível neste device. */
    fun isAvailable(): Boolean = try {
        keystore()
        true
    } catch (e: Throwable) {
        false
    }

    private fun keystore(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
}