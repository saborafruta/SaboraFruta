package br.com.ited.forcavendas.data

import br.com.ited.forcavendas.BuildConfig
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

class ApiException(val statusCode: Int, message: String) : Exception(message)
class NetworkException(message: String, cause: Throwable? = null) : Exception(message, cause)

class ItedRepository(private val store: SessionStore) {
    private val api = ItedApiClient(store)

    suspend fun login(email: String, password: String): LoginResult {
        val token = api.publicPost(
            "api/v1/mobile/token/",
            JSONObject().put("email", email.trim()).put("password", password),
        )
        val tokens = AuthTokens(token.getString("access"), token.getString("refresh"))
        val contextJson = api.publicGet(
            "api/v1/mobile/contexto/",
            accessToken = tokens.access,
        )
        val user = contextJson.getJSONObject("usuario")
        return LoginResult(
            tokens = tokens,
            usuarioNome = user.getString("nome"),
            usuarioEmail = user.getString("email"),
            contexts = contextJson.getJSONArray("contextos").mapObjects(::parseContext),
        )
    }

    fun selectContext(result: LoginResult, context: WorkContext): UserSession = UserSession(
        tokens = result.tokens,
        usuarioNome = result.usuarioNome,
        usuarioEmail = result.usuarioEmail,
        context = context,
    ).also(store::saveSession)

    fun savedSession(): UserSession? = store.loadSession()

    fun logout() = store.clearSession()

    fun pendingCount(): Int = store.pendingOrders().length()

    suspend fun clients(session: UserSession, query: String = ""): List<Client> {
        val key = "clients_${session.context.tenantAlias}_${session.context.filialId}"
        val path = "api/v1/mobile/clientes/?q=${query.encoded()}"
        val json = cachedRequest(session, key, path)
        return json.getJSONArray("clientes").mapObjects { item ->
            Client(
                id = item.getLong("id"),
                nome = item.getString("nome"),
                documento = item.optString("cpf_cnpj"),
                cidadeUf = listOf(item.optString("cidade"), item.optString("uf"))
                    .filter(String::isNotBlank).joinToString("/") ,
                creditoDisponivel = item.getString("credito_disponivel"),
                tabelaPreco = item.getString("tabela_preco"),
                prazoPagamentoDias = item.optInt("prazo_pagamento_dias"),
                bloqueado = item.optBoolean("bloqueado"),
                motivoBloqueio = item.optString("motivo_bloqueio"),
            )
        }
    }

    suspend fun products(
        session: UserSession,
        clientId: Long,
        query: String = "",
    ): List<Product> {
        val key = "products_${session.context.tenantAlias}_${session.context.filialId}_$clientId"
        val path = "api/v1/mobile/produtos/?cliente_id=$clientId&q=${query.encoded()}"
        val json = cachedRequest(session, key, path)
        return json.getJSONArray("produtos").mapObjects { item ->
            Product(
                id = item.getLong("id"),
                descricao = item.getString("descricao"),
                codigo = item.optString("codigo"),
                unidade = item.getString("unidade"),
                preco = item.getString("preco"),
                estoqueDisponivel = item.getString("estoque_disponivel"),
                controlaLote = item.optBoolean("controla_lote"),
                podeVender = item.optBoolean("pode_vender"),
                bloqueios = item.optJSONArray("bloqueios")?.mapStrings() ?: emptyList(),
                estoques = item.optJSONArray("estoques")?.mapObjects { saldo ->
                    StockBalance(
                        depositoId = saldo.getLong("deposito_id"),
                        depositoNome = saldo.getString("deposito_nome"),
                        disponivel = saldo.getString("disponivel"),
                    )
                } ?: emptyList(),
            )
        }
    }

    suspend fun orderOptions(session: UserSession): OrderOptions {
        val key = "options_${session.context.tenantAlias}_${session.context.filialId}"
        val json = cachedRequest(session, key, "api/v1/mobile/opcoes-pedido/")
        return OrderOptions(
            payments = json.getJSONArray("formas_pagamento").mapObjects {
                PaymentOption(it.getLong("id"), it.getString("descricao"))
            },
            conditions = json.getJSONArray("condicoes_pagamento").mapObjects {
                PaymentCondition(it.getLong("id"), it.getString("descricao"))
            },
        )
    }

    suspend fun submitOrder(session: UserSession, draft: OrderDraft): SubmitOrderResult {
        val idempotencyKey = UUID.randomUUID().toString()
        val payload = orderPayload(draft)
        return try {
            val response = api.authorized(
                session,
                session.context,
                "POST",
                "api/v1/mobile/pedidos/",
                payload,
                idempotencyKey,
            )
            val order = response.getJSONObject("pedido")
            SubmitOrderResult(order.getLong("id"), order.getString("numero_pedido"), false)
        } catch (_: NetworkException) {
            val pending = store.pendingOrders()
            pending.put(
                JSONObject()
                    .put("key", idempotencyKey)
                    .put("tenant_alias", session.context.tenantAlias)
                    .put("filial_id", session.context.filialId)
                    .put("payload", payload),
            )
            store.savePendingOrders(pending)
            SubmitOrderResult(null, null, true)
        }
    }

    suspend fun orders(session: UserSession): List<OrderSummary> {
        val key = "orders_${session.context.tenantAlias}_${session.context.filialId}"
        val json = cachedRequest(session, key, "api/v1/mobile/pedidos/")
        return json.getJSONArray("pedidos").mapObjects {
            OrderSummary(
                id = it.getLong("id"),
                numero = it.getString("numero_pedido"),
                cliente = it.getString("cliente_nome"),
                status = it.getString("status"),
                statusDescricao = it.getString("status_descricao"),
                valorTotal = it.getString("valor_total"),
                dataEmissao = it.getString("data_emissao"),
            )
        }
    }

    suspend fun separationQueue(session: UserSession): List<SeparationOrder> {
        val key = "separation_${session.context.tenantAlias}_${session.context.filialId}"
        val json = cachedRequest(session, key, "api/v1/mobile/separacoes/")
        return json.getJSONArray("pedidos").mapObjects { order ->
            SeparationOrder(
                id = order.getLong("id"),
                numero = order.getString("numero_pedido"),
                cliente = order.getString("cliente_nome"),
                prioridade = order.getString("prioridade"),
                dataEntrega = order.optNullableString("data_entrega_prevista"),
                status = order.getString("status"),
                lines = order.getJSONArray("itens").mapObjects { item ->
                    SeparationLine(
                        id = item.getLong("id"),
                        numeroItem = item.getInt("numero_item"),
                        produtoDescricao = item.getString("produto_descricao"),
                        produtoCodigo = item.optString("produto_codigo"),
                        unidade = item.getString("unidade"),
                        quantidadeSolicitada = item.getString("quantidade_solicitada"),
                        controlaLote = item.optBoolean("controla_lote"),
                        lotesSugeridos = item.getJSONArray("lotes_sugeridos").mapObjects { lote ->
                            BatchSuggestion(
                                id = lote.getLong("id"),
                                numero = lote.getString("numero"),
                                quantidade = lote.getString("quantidade"),
                                validade = lote.optNullableString("validade"),
                            )
                        },
                        depositos = item.getJSONArray("depositos").mapObjects { deposito ->
                            SeparationDeposit(
                                id = deposito.getLong("id"),
                                nome = deposito.getString("nome"),
                                disponivel = deposito.getString("disponivel"),
                                padrao = deposito.optBoolean("padrao"),
                            )
                        },
                    )
                },
            )
        }
    }

    suspend fun completeSeparation(
        session: UserSession,
        order: SeparationOrder,
        inputs: List<SeparationInput>,
        observation: String,
    ): String {
        val items = JSONArray()
        inputs.forEach { input ->
            val item = JSONObject()
                .put("item_id", input.itemId)
                .put("deposito_id", input.depositoId)
                .put("quantidade_separada", input.quantidade)
                .put("conferido", true)
                .put("observacao", input.observacao)
            input.batchId?.let { batchId ->
                item.put(
                    "lotes",
                    JSONArray().put(
                        JSONObject()
                            .put("lote_id", batchId)
                            .put("quantidade", input.quantidade),
                    ),
                )
            }
            items.put(item)
        }
        val response = api.authorized(
            session,
            session.context,
            "POST",
            "api/v1/mobile/separacoes/${order.id}/concluir/",
            JSONObject().put("observacao", observation).put("itens", items),
            UUID.randomUUID().toString(),
        )
        return response.getString("numero")
    }

    suspend fun sync(session: UserSession): SyncSummary {
        val pending = store.pendingOrders()
        val before = pending.length()
        val remaining = JSONArray()
        var sent = 0
        for (index in 0 until pending.length()) {
            val item = pending.getJSONObject(index)
            val sameContext = item.getString("tenant_alias") == session.context.tenantAlias &&
                item.getLong("filial_id") == session.context.filialId
            if (!sameContext) {
                remaining.put(item)
                continue
            }
            try {
                api.authorized(
                    session,
                    session.context,
                    "POST",
                    "api/v1/mobile/pedidos/",
                    item.getJSONObject("payload"),
                    item.getString("key"),
                )
                sent += 1
            } catch (_: Exception) {
                remaining.put(item)
            }
        }
        store.savePendingOrders(remaining)
        // Atualiza também os dados mais importantes para o trabalho offline.
        val suffix = "${session.context.tenantAlias}_${session.context.filialId}"
        val refreshes = listOf(
            runCatching {
                api.authorized(session, session.context, "GET", "api/v1/mobile/opcoes-pedido/")
                    .also { store.cache("options_$suffix", it) }
            },
            runCatching {
                api.authorized(session, session.context, "GET", "api/v1/mobile/clientes/")
                    .also { store.cache("clients_$suffix", it) }
            },
            runCatching {
                api.authorized(session, session.context, "GET", "api/v1/mobile/pedidos/")
                    .also { store.cache("orders_$suffix", it) }
            },
        )
        if (refreshes.all { it.isFailure }) {
            throw refreshes.first().exceptionOrNull()
                ?: NetworkException("Não foi possível sincronizar com o ITED.")
        }
        return SyncSummary(before, remaining.length(), sent)
    }

    private suspend fun cachedRequest(
        session: UserSession,
        key: String,
        path: String,
    ): JSONObject = try {
        api.authorized(session, session.context, "GET", path).also { store.cache(key, it) }
    } catch (network: NetworkException) {
        store.cached(key) ?: throw network
    }

    private fun orderPayload(draft: OrderDraft): JSONObject {
        val items = JSONArray()
        draft.lines.forEach { line ->
            items.put(
                JSONObject()
                    .put("produto_id", line.product.id)
                    .put("quantidade", line.quantity),
            )
        }
        return JSONObject()
            .put("cliente_id", draft.client.id)
            .put("forma_pagamento_id", draft.paymentId)
            .put("condicao_pagamento_id", draft.conditionId ?: JSONObject.NULL)
            .put("prazo_pagamento_dias", draft.prazoDias)
            .put("prioridade", draft.prioridade)
            .put("data_entrega_prevista", draft.dataEntrega ?: JSONObject.NULL)
            .put("observacao", draft.observacao)
            .put("itens", items)
    }
}

private class ItedApiClient(private val store: SessionStore) {
    private val baseUrl = BuildConfig.ITED_API_BASE_URL.trimEnd('/') + "/"

    suspend fun publicPost(path: String, body: JSONObject): JSONObject = request(
        method = "POST",
        path = path,
        body = body,
    )

    suspend fun publicGet(path: String, accessToken: String): JSONObject = request(
        method = "GET",
        path = path,
        accessToken = accessToken,
    )

    suspend fun authorized(
        session: UserSession,
        context: WorkContext,
        method: String,
        path: String,
        body: JSONObject? = null,
        idempotencyKey: String? = null,
    ): JSONObject {
        val currentTokens = store.loadSession()?.tokens ?: session.tokens
        return try {
            request(method, path, body, currentTokens.access, context, idempotencyKey)
        } catch (unauthorized: ApiException) {
            if (unauthorized.statusCode != HttpURLConnection.HTTP_UNAUTHORIZED) throw unauthorized
            val refreshed = request(
                "POST",
                "api/v1/mobile/token/refresh/",
                JSONObject().put("refresh", currentTokens.refresh),
            )
            val tokens = AuthTokens(
                access = refreshed.getString("access"),
                refresh = refreshed.optString("refresh", currentTokens.refresh),
            )
            store.updateTokens(tokens)
            request(method, path, body, tokens.access, context, idempotencyKey)
        }
    }

    private suspend fun request(
        method: String,
        path: String,
        body: JSONObject? = null,
        accessToken: String? = null,
        context: WorkContext? = null,
        idempotencyKey: String? = null,
    ): JSONObject = withContext(Dispatchers.IO) {
        val connection = try {
            URI(baseUrl + path.trimStart('/')).toURL().openConnection() as HttpURLConnection
        } catch (error: Exception) {
            throw NetworkException("Não foi possível preparar a conexão com o ITED.", error)
        }
        try {
            connection.requestMethod = method
            connection.connectTimeout = 15_000
            connection.readTimeout = 25_000
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            accessToken?.let { connection.setRequestProperty("Authorization", "Bearer $it") }
            context?.let {
                connection.setRequestProperty("X-Tenant-Alias", it.tenantAlias)
                connection.setRequestProperty("X-Filial-ID", it.filialId.toString())
            }
            idempotencyKey?.let { connection.setRequestProperty("Idempotency-Key", it) }
            if (body != null) {
                connection.doOutput = true
                connection.outputStream.use { output ->
                    output.write(body.toString().toByteArray(StandardCharsets.UTF_8))
                }
            }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val raw = stream?.bufferedReader(StandardCharsets.UTF_8)?.use { it.readText() }.orEmpty()
            val json = if (raw.isBlank()) JSONObject() else runCatching { JSONObject(raw) }
                .getOrElse { JSONObject().put("detail", raw) }
            if (status !in 200..299) {
                throw ApiException(status, extractMessage(json))
            }
            json
        } catch (api: ApiException) {
            throw api
        } catch (io: IOException) {
            throw NetworkException("Sem conexão com o ITED. Verifique sua internet.", io)
        } finally {
            connection.disconnect()
        }
    }

    private fun extractMessage(json: JSONObject): String {
        val detail = json.opt("detail") ?: json.opt("non_field_errors")
        if (detail != null && detail != JSONObject.NULL) return detail.toString()
        val keys = json.keys()
        if (keys.hasNext()) {
            val key = keys.next()
            return "$key: ${json.opt(key)}"
        }
        return "O ITED recusou a operação."
    }
}

private fun String.encoded(): String = URLEncoder.encode(this, StandardCharsets.UTF_8.toString())

private inline fun <T> JSONArray.mapObjects(transform: (JSONObject) -> T): List<T> =
    (0 until length()).map { transform(getJSONObject(it)) }

private fun JSONArray.mapStrings(): List<String> =
    (0 until length()).map { getString(it) }

private fun JSONObject.optNullableString(key: String): String? =
    if (isNull(key)) null else optString(key).takeIf(String::isNotBlank)

private fun parseContext(json: JSONObject): WorkContext {
    val permissions = json.getJSONObject("permissoes")
    return WorkContext(
        tenantAlias = json.getString("tenant_alias"),
        empresaId = json.getLong("empresa_id"),
        empresaNome = json.getString("empresa_nome"),
        filialId = json.getLong("filial_id"),
        filialNome = json.getString("filial_nome"),
        filialCnpj = json.getString("filial_cnpj"),
        podeCriarPedido = permissions.optBoolean("pedidos_criar"),
        podeSeparar = permissions.optBoolean("separacao_editar"),
    )
}
