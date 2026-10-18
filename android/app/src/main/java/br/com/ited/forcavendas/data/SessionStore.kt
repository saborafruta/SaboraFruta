package br.com.ited.forcavendas.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import org.json.JSONArray
import org.json.JSONObject

class SessionStore(context: Context) {
    private val preferences = context.getSharedPreferences("ited_mobile", Context.MODE_PRIVATE)
    private var transientTokens: AuthTokens? = null

    init {
        preferences.edit()
            .remove("access")
            .remove("refresh")
            .remove("user_name")
            .remove("user_email")
            .remove("context")
            .apply()
    }

    fun saveSession(session: UserSession) {
        transientTokens = session.tokens
        val payload = JSONObject()
            .put("access", session.tokens.access)
            .put("refresh", session.tokens.refresh)
            .put("user_name", session.usuarioNome)
            .put("user_email", session.usuarioEmail)
            .put("context", contextToJson(session.context))
            .toString()
        preferences.edit()
            .putString(ENCRYPTED_SESSION, encrypt(payload))
            .remove("access")
            .remove("refresh")
            .remove("user_name")
            .remove("user_email")
            .remove("context")
            .apply()
    }

    fun loadSession(): UserSession? {
        val encrypted = preferences.getString(ENCRYPTED_SESSION, null) ?: return null
        return runCatching {
            val json = JSONObject(decrypt(encrypted))
            UserSession(
                tokens = AuthTokens(json.getString("access"), json.getString("refresh")),
                usuarioNome = json.optString("user_name"),
                usuarioEmail = json.optString("user_email"),
                context = contextFromJson(json.getJSONObject("context")),
            )
        }.getOrNull()
    }

    fun updateTokens(tokens: AuthTokens) {
        transientTokens = tokens
        loadSession()?.let { saveSession(it.copy(tokens = tokens)) }
    }

    fun currentTokens(fallback: AuthTokens): AuthTokens =
        loadSession()?.tokens ?: transientTokens ?: fallback

    fun clearSession() {
        transientTokens = null
        preferences.edit()
            .remove(ENCRYPTED_SESSION)
            .remove(SECURE_ACCESS)
            .remove("access")
            .remove("refresh")
            .remove("user_name")
            .remove("user_email")
            .remove("context")
            .apply()
    }

    fun secureAccessEnabled(): Boolean = preferences.getBoolean(SECURE_ACCESS, false)

    fun setSecureAccessEnabled(enabled: Boolean) {
        preferences.edit().putBoolean(SECURE_ACCESS, enabled).apply()
    }

    fun darkThemePreference(): Boolean? = if (preferences.contains("dark_theme")) {
        preferences.getBoolean("dark_theme", false)
    } else {
        null
    }

    fun saveDarkThemePreference(enabled: Boolean) {
        preferences.edit().putBoolean("dark_theme", enabled).apply()
    }

    fun pendingOrders(): JSONArray = runCatching {
        JSONArray(preferences.getString("pending_orders", "[]"))
    }.getOrDefault(JSONArray())

    fun savePendingOrders(items: JSONArray) {
        preferences.edit().putString("pending_orders", items.toString()).apply()
    }

    fun cache(key: String, value: JSONObject) {
        preferences.edit().putString("cache_$key", value.toString()).apply()
    }

    fun cached(key: String): JSONObject? = runCatching {
        preferences.getString("cache_$key", null)?.let(::JSONObject)
    }.getOrNull()

    fun hasCache(key: String): Boolean = preferences.contains("cache_$key")

    fun hasCacheStartingWith(prefix: String): Boolean =
        preferences.all.keys.any { it.startsWith("cache_$prefix") }

    fun saveSyncSuccess(context: WorkContext, userEmail: String, at: String) {
        preferences.edit()
            .putString(userKey("last_success", context, userEmail), at)
            .remove(userKey("last_error", context, userEmail))
            .apply()
    }

    fun saveSyncError(context: WorkContext, userEmail: String, message: String) {
        preferences.edit()
            .putString(userKey("last_error", context, userEmail), message.take(500))
            .apply()
    }

    fun lastSyncSuccess(context: WorkContext, userEmail: String): String? =
        preferences.getString(userKey("last_success", context, userEmail), null)

    fun lastSyncError(context: WorkContext, userEmail: String): String? =
        preferences.getString(userKey("last_error", context, userEmail), null)

    fun saveOrderDraft(context: WorkContext, userEmail: String, draft: JSONObject) {
        preferences.edit()
            .putString(userKey("order_draft", context, userEmail), draft.toString())
            .apply()
    }

    fun orderDraft(context: WorkContext, userEmail: String): JSONObject? = runCatching {
        preferences.getString(userKey("order_draft", context, userEmail), null)?.let(::JSONObject)
    }.getOrNull()

    fun clearOrderDraft(context: WorkContext, userEmail: String) {
        preferences.edit().remove(userKey("order_draft", context, userEmail)).apply()
    }

    private fun syncKey(prefix: String, context: WorkContext): String =
        "${prefix}_${context.tenantAlias}_${context.filialId}"

    private fun userKey(prefix: String, context: WorkContext, userEmail: String): String =
        "${syncKey(prefix, context)}_${userEmail.trim().lowercase().hashCode().toUInt().toString(16)}"

    private fun contextToJson(context: WorkContext) = JSONObject()
        .put("tenant_alias", context.tenantAlias)
        .put("empresa_id", context.empresaId)
        .put("empresa_nome", context.empresaNome)
        .put("filial_id", context.filialId)
        .put("filial_nome", context.filialNome)
        .put("filial_cnpj", context.filialCnpj)
        .put("pedidos_criar", context.podeCriarPedido)
        .put("separacao_editar", context.podeSeparar)
        .put("checkout_vender", context.podeCheckout)

    private fun contextFromJson(json: JSONObject) = WorkContext(
        tenantAlias = json.getString("tenant_alias"),
        empresaId = json.getLong("empresa_id"),
        empresaNome = json.getString("empresa_nome"),
        filialId = json.getLong("filial_id"),
        filialNome = json.getString("filial_nome"),
        filialCnpj = json.getString("filial_cnpj"),
        podeCriarPedido = json.optBoolean("pedidos_criar", false),
        podeSeparar = json.optBoolean("separacao_editar", false),
        // Sessões gravadas antes da 0.8.0 não possuem esta chave. Exibimos a
        // entrada na primeira atualização; a API ainda aplica o RBAC real.
        podeCheckout = json.optBoolean("checkout_vender", true),
    )

    private fun encrypt(value: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, encryptionKey())
        val encrypted = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(cipher.iv + encrypted, Base64.NO_WRAP)
    }

    private fun decrypt(value: String): String {
        val packed = Base64.decode(value, Base64.NO_WRAP)
        require(packed.size > IV_SIZE) { "Sessão protegida inválida" }
        val iv = packed.copyOfRange(0, IV_SIZE)
        val encrypted = packed.copyOfRange(IV_SIZE, packed.size)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, encryptionKey(), GCMParameterSpec(128, iv))
        return cipher.doFinal(encrypted).toString(Charsets.UTF_8)
    }

    private fun encryptionKey(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
            .apply {
                init(
                    KeyGenParameterSpec.Builder(
                        KEY_ALIAS,
                        KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                    )
                        .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                        .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                        .setKeySize(256)
                        .build(),
                )
            }
            .generateKey()
    }

    private companion object {
        const val ENCRYPTED_SESSION = "encrypted_session_v1"
        const val SECURE_ACCESS = "secure_access_enabled"
        const val KEY_ALIAS = "ited_secure_session_key"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val IV_SIZE = 12
    }
}
