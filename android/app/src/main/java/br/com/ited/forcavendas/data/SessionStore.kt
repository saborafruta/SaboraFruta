package br.com.ited.forcavendas.data

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

class SessionStore(context: Context) {
    private val preferences = context.getSharedPreferences("ited_mobile", Context.MODE_PRIVATE)

    fun saveSession(session: UserSession) {
        preferences.edit()
            .putString("access", session.tokens.access)
            .putString("refresh", session.tokens.refresh)
            .putString("user_name", session.usuarioNome)
            .putString("user_email", session.usuarioEmail)
            .putString("context", contextToJson(session.context).toString())
            .apply()
    }

    fun loadSession(): UserSession? {
        val access = preferences.getString("access", null) ?: return null
        val refresh = preferences.getString("refresh", null) ?: return null
        val contextRaw = preferences.getString("context", null) ?: return null
        return runCatching {
            UserSession(
                tokens = AuthTokens(access, refresh),
                usuarioNome = preferences.getString("user_name", "") ?: "",
                usuarioEmail = preferences.getString("user_email", "") ?: "",
                context = contextFromJson(JSONObject(contextRaw)),
            )
        }.getOrNull()
    }

    fun updateTokens(tokens: AuthTokens) {
        preferences.edit()
            .putString("access", tokens.access)
            .putString("refresh", tokens.refresh)
            .apply()
    }

    fun clearSession() {
        preferences.edit()
            .remove("access")
            .remove("refresh")
            .remove("user_name")
            .remove("user_email")
            .remove("context")
            .apply()
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

    private fun contextFromJson(json: JSONObject) = WorkContext(
        tenantAlias = json.getString("tenant_alias"),
        empresaId = json.getLong("empresa_id"),
        empresaNome = json.getString("empresa_nome"),
        filialId = json.getLong("filial_id"),
        filialNome = json.getString("filial_nome"),
        filialCnpj = json.getString("filial_cnpj"),
        podeCriarPedido = json.optBoolean("pedidos_criar", false),
        podeSeparar = json.optBoolean("separacao_editar", false),
    )
}
