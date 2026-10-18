package br.com.ited.forcavendas.data

import br.com.ited.forcavendas.BuildConfig
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder
import java.nio.charset.StandardCharsets
import java.time.Instant
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

    fun selectContext(
        result: LoginResult,
        context: WorkContext,
        secureAccess: Boolean? = null,
    ): UserSession = UserSession(
        tokens = result.tokens,
        usuarioNome = result.usuarioNome,
        usuarioEmail = result.usuarioEmail,
        context = context,
    ).also { session ->
        val shouldPersist = secureAccess ?: store.secureAccessEnabled()
        store.setSecureAccessEnabled(shouldPersist)
        if (shouldPersist) store.saveSession(session) else store.clearSession()
    }

    suspend fun contexts(session: UserSession): LoginResult {
        val contextJson = api.authorized(
            session = session,
            context = session.context,
            method = "GET",
            path = "api/v1/mobile/contexto/",
        )
        val user = contextJson.getJSONObject("usuario")
        val currentTokens = store.currentTokens(session.tokens)
        return LoginResult(
            tokens = currentTokens,
            usuarioNome = user.getString("nome"),
            usuarioEmail = user.getString("email"),
            contexts = contextJson.getJSONArray("contextos").mapObjects(::parseContext),
        )
    }

    fun savedSession(): UserSession? = if (store.secureAccessEnabled()) {
        store.loadSession()
    } else {
        null
    }

    fun logout() = store.clearSession()

    fun darkThemePreference(): Boolean? = store.darkThemePreference()

    fun saveDarkThemePreference(enabled: Boolean) = store.saveDarkThemePreference(enabled)

    fun pendingCount(session: UserSession): Int = pendingItems(session).size

    fun syncDashboard(session: UserSession): SyncDashboard {
        val suffix = "${session.context.tenantAlias}_${session.context.filialId}"
        val cached = buildList {
            if (store.hasCache("clients_${suffix}_${queryCacheKey("")}")) add("Clientes")
            if (store.hasCacheStartingWith("products_${suffix}_")) add("Produtos e preços")
            if (store.hasCache("options_$suffix")) add("Pagamentos")
            if (store.hasCache("orders_$suffix")) add("Pedidos")
            if (store.hasCache("separation_$suffix")) add("Separações")
        }
        return SyncDashboard(
            lastSuccessAt = store.lastSyncSuccess(session.context, session.usuarioEmail),
            lastError = store.lastSyncError(session.context, session.usuarioEmail),
            hasOrderDraft = store.orderDraft(session.context, session.usuarioEmail) != null,
            pendingOrders = pendingItems(session).map { item ->
                PendingOrderInfo(
                    key = item.optString("key"),
                    clientName = item.optString("client_name", "Cliente não identificado"),
                    createdAt = item.optString("created_at"),
                    attempts = item.optInt("attempts", 0),
                    lastError = item.optString("last_error").takeIf(String::isNotBlank),
                )
            },
            cachedResources = cached,
        )
    }

    fun savedOrderDraft(session: UserSession): SavedOrderDraft? = runCatching {
        store.orderDraft(session.context, session.usuarioEmail)?.let { json ->
            SavedOrderDraft(
                client = parseClient(json.getJSONObject("client")),
                paymentId = json.optNullableLong("payment_id"),
                conditionId = json.optNullableLong("condition_id"),
                priority = json.optString("priority", "normal"),
                deliveryDate = json.optString("delivery_date"),
                observation = json.optString("observation"),
                savedAt = json.optString("saved_at"),
                lines = json.getJSONArray("lines").mapObjects { line ->
                    OrderLineDraft(
                        product = parseProduct(line.getJSONObject("product")),
                        quantity = line.getString("quantity"),
                    )
                },
            )
        }
    }.getOrNull()

    fun saveOrderDraft(session: UserSession, draft: SavedOrderDraft) {
        val lines = JSONArray()
        draft.lines.forEach { line ->
            lines.put(
                JSONObject()
                    .put("product", productToJson(line.product))
                    .put("quantity", line.quantity),
            )
        }
        store.saveOrderDraft(
            session.context,
            session.usuarioEmail,
            JSONObject()
                .put("client", clientToJson(draft.client))
                .put("payment_id", draft.paymentId ?: JSONObject.NULL)
                .put("condition_id", draft.conditionId ?: JSONObject.NULL)
                .put("priority", draft.priority)
                .put("delivery_date", draft.deliveryDate)
                .put("observation", draft.observation)
                .put("saved_at", Instant.now().toString())
                .put("lines", lines),
        )
    }

    fun clearOrderDraft(session: UserSession) = store.clearOrderDraft(
        session.context,
        session.usuarioEmail,
    )

    suspend fun clients(session: UserSession, query: String = ""): List<Client> {
        val key = "clients_${session.context.tenantAlias}_${session.context.filialId}_${queryCacheKey(query)}"
        val path = "api/v1/mobile/clientes/?q=${query.encoded()}"
        val json = cachedRequest(session, key, path)
        return json.getJSONArray("clientes").mapObjects(::parseClient)
    }

    suspend fun createClient(session: UserSession, registration: ClientRegistration): Client {
        val json = api.authorized(
            session,
            session.context,
            "POST",
            "api/v1/mobile/clientes/",
            registration.toJson(),
        )
        return parseClient(json.getJSONObject("cliente"))
    }

    suspend fun lookupCep(session: UserSession, cep: String): CepAddress {
        val json = api.authorized(
            session,
            session.context,
            "GET",
            "api/v1/mobile/clientes/cep/?cep=${cep.encoded()}",
        ).getJSONObject("endereco")
        return CepAddress(
            postalCode = json.optString("cep"),
            street = json.optString("endereco"),
            complement = json.optString("complemento"),
            neighborhood = json.optString("bairro"),
            city = json.optString("cidade"),
            state = json.optString("uf"),
            cityCode = json.optString("codigo_municipio_ibge"),
        )
    }

    suspend fun lookupCompany(session: UserSession, cnpj: String): CompanyLookup {
        val json = api.authorized(
            session,
            session.context,
            "GET",
            "api/v1/mobile/clientes/cnpj/?cnpj=${cnpj.encoded()}",
        ).getJSONObject("empresa")
        return CompanyLookup(
            document = json.optString("cnpj"),
            legalName = json.optString("razao_social"),
            tradeName = json.optString("nome_fantasia"),
            stateRegistration = json.optString("inscricao_estadual"),
            street = json.optString("endereco"),
            number = json.optString("numero"),
            complement = json.optString("complemento"),
            neighborhood = json.optString("bairro"),
            city = json.optString("cidade"),
            state = json.optString("uf"),
            postalCode = json.optString("cep"),
            cityCode = json.optString("codigo_municipio_ibge"),
            phone = json.optString("telefone"),
            email = json.optString("email"),
            registrationStatus = json.optString("situacao_cadastral"),
        )
    }

    suspend fun checkoutState(session: UserSession): CheckoutState {
        val json = api.authorized(
            session,
            session.context,
            "GET",
            "api/v1/mobile/checkout/estado/",
        )
        val cashSession = json.optJSONObject("sessao")
        val discountPolicy = json.optJSONObject("descontos") ?: JSONObject()
        return CheckoutState(
            sessionOpen = cashSession != null,
            cashRegisterName = cashSession?.optString("caixa_nome")?.takeIf(String::isNotBlank),
            payments = json.getJSONArray("formas_pagamento").mapObjects {
                PaymentOption(it.getLong("id"), it.getString("descricao"), it.optString("tipo"))
            },
            canApplyDiscount = json.optBoolean("pode_aplicar_desconto"),
            discountPercentages = discountPolicy.optJSONArray("percentuais")?.mapStrings() ?: emptyList(),
            maxDiscountPercent = discountPolicy.optString("percentual_maximo", "0"),
            maxRoundingDiscount = discountPolicy.optString("arredondamento_maximo", "0.00"),
        )
    }

    suspend fun checkoutClients(session: UserSession, query: String): List<Client> {
        val json = api.authorized(
            session,
            session.context,
            "GET",
            "api/v1/mobile/checkout/clientes/?q=${query.encoded()}",
        )
        return json.getJSONArray("clientes").mapObjects(::parseClient)
    }

    suspend fun createCheckoutClient(
        session: UserSession,
        name: String,
        document: String,
        phone: String,
    ): Client {
        val json = api.authorized(
            session,
            session.context,
            "POST",
            "api/v1/mobile/checkout/clientes/",
            JSONObject()
                .put("nome", name.trim())
                .put("cpf_cnpj", document.trim())
                .put("celular", phone.trim()),
        )
        return parseClient(json.getJSONObject("cliente"))
    }

    suspend fun checkoutProducts(
        session: UserSession,
        query: String,
        clientId: Long?,
    ): List<Product> {
        val path = buildString {
            append("api/v1/mobile/checkout/produtos/?q=${query.encoded()}")
            clientId?.let { append("&cliente_id=$it") }
        }
        val json = api.authorized(session, session.context, "GET", path)
        return json.getJSONArray("produtos").mapObjects(::parseProduct)
    }

    suspend fun submitCheckout(session: UserSession, sale: CheckoutSale): CheckoutResult {
        val items = checkoutItems(sale.lines)
        val payments = JSONArray().put(
            JSONObject()
                .put("forma_id", sale.payment.id)
                .put("valor", sale.received),
        )
        val response = api.authorized(
            session,
            session.context,
            "POST",
            "api/v1/mobile/checkout/vendas/",
            JSONObject()
                .put("pendencia_id", sale.pendingId ?: JSONObject.NULL)
                .put("cliente_id", sale.client?.id ?: JSONObject.NULL)
                .put("desconto", sale.discount)
                .put("autorizacao_desconto", sale.discountAuthorization)
                .put("itens", items)
                .put("pagamentos", payments),
            UUID.randomUUID().toString(),
        )
        return CheckoutResult(
            saleId = response.getLong("venda_id"),
            number = response.getString("numero_venda"),
            total = response.getString("valor_total"),
            change = response.getString("troco"),
        )
    }

    suspend fun saveCheckoutPending(
        session: UserSession,
        client: Client?,
        lines: List<CheckoutLine>,
        discount: String,
        payment: PaymentOption?,
        received: String,
        discountAuthorization: String = "",
    ): CheckoutPendingSummary {
        val payments = JSONArray()
        if (payment != null) {
            payments.put(
                JSONObject()
                    .put("forma_id", payment.id)
                    .put("valor", received),
            )
        }
        val response = api.authorized(
            session,
            session.context,
            "POST",
            "api/v1/mobile/checkout/pendencias/",
            JSONObject()
                .put("cliente_id", client?.id ?: JSONObject.NULL)
                .put("desconto", discount)
                .put("autorizacao_desconto", discountAuthorization)
                .put("itens", checkoutItems(lines))
                .put("pagamentos", payments),
            UUID.randomUUID().toString(),
        )
        return parseCheckoutPendingSummary(response)
    }

    suspend fun authorizeCheckoutDiscount(
        session: UserSession,
        email: String,
        password: String,
    ): String {
        val response = api.authorized(
            session,
            session.context,
            "POST",
            "api/v1/mobile/checkout/descontos/autorizar/",
            JSONObject()
                .put("email", email.trim())
                .put("senha", password),
        )
        return response.getString("token")
    }

    suspend fun checkoutPendings(session: UserSession): List<CheckoutPendingSummary> {
        val response = api.authorized(
            session,
            session.context,
            "GET",
            "api/v1/mobile/checkout/pendencias/",
        )
        return response.getJSONArray("pendencias").mapObjects(::parseCheckoutPendingSummary)
    }

    suspend fun checkoutPendingDetail(session: UserSession, pendingId: Long): CheckoutPendingDraft {
        val response = api.authorized(
            session,
            session.context,
            "GET",
            "api/v1/mobile/checkout/pendencias/$pendingId/",
        )
        return CheckoutPendingDraft(
            id = response.getLong("id"),
            number = response.getString("numero_venda"),
            client = response.optJSONObject("cliente")?.let(::parseClient),
            lines = response.getJSONArray("itens").mapObjects { item ->
                CheckoutLine(
                    product = parseProduct(item.getJSONObject("produto")),
                    quantity = item.getString("quantidade"),
                )
            },
            discount = response.getString("desconto"),
            paymentId = response.optNullableLong("forma_pagamento_id"),
        )
    }

    suspend fun products(
        session: UserSession,
        clientId: Long,
        query: String = "",
        categoryId: Long? = null,
        highlightsOnly: Boolean = false,
    ): List<Product> {
        val filterKey = when {
            highlightsOnly -> "highlights"
            categoryId != null -> "category_$categoryId"
            else -> "all"
        }
        val key = "products_${session.context.tenantAlias}_${session.context.filialId}_${clientId}_${filterKey}_${queryCacheKey(query)}"
        val filters = buildString {
            append("&q=${query.encoded()}")
            if (highlightsOnly) append("&destaques=1")
            else if (categoryId != null) append("&categoria_id=$categoryId")
        }
        val path = "api/v1/mobile/produtos/?cliente_id=$clientId$filters"
        val json = cachedRequest(session, key, path)
        return json.getJSONArray("produtos").mapObjects(::parseProduct)
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
            categories = json.optJSONArray("categorias")?.mapObjects {
                ProductCategory(it.getLong("id"), it.getString("nome"))
            } ?: emptyList(),
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
                    .put("user_email", session.usuarioEmail.trim().lowercase())
                    .put("client_name", draft.client.nome)
                    .put("created_at", Instant.now().toString())
                    .put("attempts", 0)
                    .put("last_error", "Aguardando conexão para o primeiro envio.")
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
            if (input.batchAllocations.isNotEmpty()) {
                val allocations = JSONArray()
                input.batchAllocations.forEach { allocation ->
                    allocations.put(
                        JSONObject()
                            .put("lote_id", allocation.batchId)
                            .put("quantidade", allocation.quantity),
                    )
                }
                item.put(
                    "lotes",
                    allocations,
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
        val before = pendingItems(session).size
        val pending = store.pendingOrders()
        val remaining = JSONArray()
        var sent = 0
        val errors = mutableListOf<String>()
        for (index in 0 until pending.length()) {
            val item = pending.getJSONObject(index)
            val sameContext = item.getString("tenant_alias") == session.context.tenantAlias &&
                item.getLong("filial_id") == session.context.filialId &&
                item.optString("user_email") == session.usuarioEmail.trim().lowercase()
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
            } catch (error: Exception) {
                item.put("attempts", item.optInt("attempts", 0) + 1)
                item.put("last_error", (error.message ?: "Falha ao enviar pedido.").take(500))
                remaining.put(item)
                errors += item.getString("last_error")
            }
        }
        store.savePendingOrders(remaining)
        // Atualiza também os dados mais importantes para o trabalho offline.
        val suffix = "${session.context.tenantAlias}_${session.context.filialId}"
        val refreshes = listOf(
            "Pagamentos" to runCatching {
                api.authorized(session, session.context, "GET", "api/v1/mobile/opcoes-pedido/")
                    .also { store.cache("options_$suffix", it) }
            },
            "Clientes" to runCatching {
                api.authorized(session, session.context, "GET", "api/v1/mobile/clientes/")
                    .also { store.cache("clients_${suffix}_${queryCacheKey("")}", it) }
            },
            "Pedidos" to runCatching {
                api.authorized(session, session.context, "GET", "api/v1/mobile/pedidos/")
                    .also { store.cache("orders_$suffix", it) }
            },
            "Separações" to runCatching {
                api.authorized(session, session.context, "GET", "api/v1/mobile/separacoes/")
                    .also { store.cache("separation_$suffix", it) }
            },
        )
        val refreshErrors = refreshes.mapNotNull { (resource, result) ->
            result.exceptionOrNull()?.let { "$resource: ${it.message ?: "falha"}" }
        }
        if (refreshes.all { it.second.isFailure }) {
            val failure = refreshes.first().second.exceptionOrNull()
            store.saveSyncError(
                session.context,
                session.usuarioEmail,
                failure?.message ?: "Não foi possível sincronizar.",
            )
            throw failure
                ?: NetworkException("Não foi possível sincronizar com o ITED.")
        }
        val allErrors = errors + refreshErrors
        store.saveSyncSuccess(session.context, session.usuarioEmail, Instant.now().toString())
        if (allErrors.isNotEmpty()) {
            store.saveSyncError(
                session.context,
                session.usuarioEmail,
                allErrors.joinToString(" • "),
            )
        }
        return SyncSummary(
            pendingBefore = before,
            pendingAfter = pendingItems(session).size,
            sentOrders = sent,
            refreshedResources = refreshes.filter { it.second.isSuccess }.map { it.first },
            errors = allErrors,
        )
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

    private fun checkoutItems(lines: List<CheckoutLine>): JSONArray = JSONArray().also { items ->
        lines.forEach { line ->
            items.put(
                JSONObject()
                    .put("produto_id", line.product.id)
                    .put("quantidade", line.quantity),
            )
        }
    }

    private fun parseCheckoutPendingSummary(item: JSONObject) = CheckoutPendingSummary(
        id = item.getLong("id"),
        number = item.getString("numero_venda"),
        clientName = item.getString("cliente_nome"),
        total = item.getString("valor_total"),
        discount = item.optString("desconto", "0.00"),
        createdAt = item.getString("data_venda"),
        itemCount = item.getInt("quantidade_itens"),
    )

    private fun pendingItems(session: UserSession): List<JSONObject> {
        val pending = store.pendingOrders()
        var migrated = false
        val items = (0 until pending.length()).map { pending.getJSONObject(it) }
        items.forEach { item ->
            val sameContext = item.optString("tenant_alias") == session.context.tenantAlias &&
                item.optLong("filial_id") == session.context.filialId
            if (sameContext && !item.has("user_email")) {
                item.put("user_email", session.usuarioEmail.trim().lowercase())
                migrated = true
            }
        }
        if (migrated) store.savePendingOrders(pending)
        return items.filter { item ->
            item.optString("tenant_alias") == session.context.tenantAlias &&
                item.optLong("filial_id") == session.context.filialId &&
                item.optString("user_email") == session.usuarioEmail.trim().lowercase()
        }
    }

    private fun parseClient(item: JSONObject): Client = Client(
        id = item.getLong("id"),
        nome = item.getString("nome"),
        documento = item.optString("cpf_cnpj", item.optString("documento")),
        cidadeUf = item.optString("cidade_uf").ifBlank {
            listOf(item.optString("cidade"), item.optString("uf"))
                .filter(String::isNotBlank).joinToString("/")
        },
        creditoDisponivel = item.getString("credito_disponivel"),
        tabelaPreco = item.getString("tabela_preco"),
        prazoPagamentoDias = item.optInt("prazo_pagamento_dias"),
        bloqueado = item.optBoolean("bloqueado"),
        motivoBloqueio = item.optString("motivo_bloqueio"),
    )

    private fun ClientRegistration.toJson(): JSONObject = JSONObject()
        .put("tipo_pessoa", personType)
        .put("tipo", customerType)
        .put("razao_social", legalName.trim())
        .put("nome_fantasia", tradeName.trim())
        .put("cpf_cnpj", document.trim())
        .put("rg_ie", identity.trim())
        .put("inscricao_municipal", municipalRegistration.trim())
        .put("inscricao_estadual", stateRegistration.trim())
        .put("data_nascimento", birthDate.trim().ifBlank { JSONObject.NULL })
        .put("sexo", gender)
        .put("telefone", phone.trim())
        .put("celular", mobile.trim())
        .put("email", email.trim())
        .put("email_nfe", invoiceEmail.trim())
        .put("contato_nome", contactName.trim())
        .put("cep", postalCode.trim())
        .put("endereco", street.trim())
        .put("numero", number.trim())
        .put("complemento", complement.trim())
        .put("ponto_referencia", referencePoint.trim())
        .put("bairro", neighborhood.trim())
        .put("cidade", city.trim())
        .put("uf", state.trim())
        .put("codigo_municipio_ibge", cityCode.trim())
        .put("limite_credito", creditLimit.trim().replace(',', '.').ifBlank { "0" })
        .put("prazo_pagamento_dias", paymentTermDays.toIntOrNull() ?: 0)
        .put("consumidor_final", finalConsumer)
        .put("contribuinte_icms", icmsTaxpayer)
        .put("optante_simples", simpleTaxpayer)
        .put("bloqueado", blocked)
        .put("motivo_bloqueio", blockReason.trim())
        .put("observacao", notes.trim())

    private fun parseProduct(item: JSONObject): Product = Product(
        id = item.getLong("id"),
        descricao = item.getString("descricao"),
        codigo = item.optString("codigo"),
        unidade = item.getString("unidade"),
        preco = item.getString("preco"),
        precoOrigem = item.optString("preco_origem"),
        categoria = item.optString("categoria", "Sem categoria"),
        destaque = item.optBoolean("destaque"),
        estoqueDisponivel = item.getString("estoque_disponivel"),
        controlaLote = item.optBoolean("controla_lote"),
        podeVender = item.optBoolean("pode_vender"),
        bloqueios = item.optJSONArray("bloqueios")?.mapStrings() ?: emptyList(),
        estoques = item.optJSONArray("estoques")?.mapObjects { saldo ->
            StockBalance(
                depositoId = saldo.getLong("deposito_id"),
                depositoNome = saldo.getString("deposito_nome"),
                disponivel = saldo.getString("disponivel"),
                permiteVenda = saldo.optBoolean("permite_venda"),
                origemPedido = saldo.optBoolean("origem_pedido"),
            )
        } ?: emptyList(),
    )

    private fun clientToJson(client: Client): JSONObject = JSONObject()
        .put("id", client.id)
        .put("nome", client.nome)
        .put("documento", client.documento)
        .put("cidade_uf", client.cidadeUf)
        .put("credito_disponivel", client.creditoDisponivel)
        .put("tabela_preco", client.tabelaPreco)
        .put("prazo_pagamento_dias", client.prazoPagamentoDias)
        .put("bloqueado", client.bloqueado)
        .put("motivo_bloqueio", client.motivoBloqueio)

    private fun productToJson(product: Product): JSONObject {
        val stocks = JSONArray()
        product.estoques.forEach { stock ->
            stocks.put(
                JSONObject()
                    .put("deposito_id", stock.depositoId)
                    .put("deposito_nome", stock.depositoNome)
                    .put("disponivel", stock.disponivel)
                    .put("permite_venda", stock.permiteVenda)
                    .put("origem_pedido", stock.origemPedido),
            )
        }
        return JSONObject()
            .put("id", product.id)
            .put("descricao", product.descricao)
            .put("codigo", product.codigo)
            .put("unidade", product.unidade)
            .put("preco", product.preco)
            .put("preco_origem", product.precoOrigem)
            .put("categoria", product.categoria)
            .put("destaque", product.destaque)
            .put("estoque_disponivel", product.estoqueDisponivel)
            .put("controla_lote", product.controlaLote)
            .put("pode_vender", product.podeVender)
            .put("bloqueios", JSONArray(product.bloqueios))
            .put("estoques", stocks)
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
        val currentTokens = store.currentTokens(session.tokens)
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

private fun queryCacheKey(query: String): String =
    query.trim().lowercase().hashCode().toUInt().toString(16)

private inline fun <T> JSONArray.mapObjects(transform: (JSONObject) -> T): List<T> =
    (0 until length()).map { transform(getJSONObject(it)) }

private fun JSONArray.mapStrings(): List<String> =
    (0 until length()).map { getString(it) }

private fun JSONObject.optNullableString(key: String): String? =
    if (isNull(key)) null else optString(key).takeIf(String::isNotBlank)

private fun JSONObject.optNullableLong(key: String): Long? =
    if (isNull(key) || !has(key)) null else getLong(key)

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
        podeCheckout = permissions.optBoolean("checkout_vender"),
    )
}
