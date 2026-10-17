package br.com.ited.forcavendas.feature.order

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.AddShoppingCart
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import br.com.ited.forcavendas.data.Client
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.OrderDraft
import br.com.ited.forcavendas.data.OrderLineDraft
import br.com.ited.forcavendas.data.OrderOptions
import br.com.ited.forcavendas.data.Product
import br.com.ited.forcavendas.data.SavedOrderDraft
import br.com.ited.forcavendas.data.UserSession
import java.math.BigDecimal
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NewOrderScreen(
    repository: ItedRepository,
    session: UserSession,
    onBack: () -> Unit,
    onSubmitted: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var loading by remember { mutableStateOf(true) }
    var submitting by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var clients by remember { mutableStateOf(emptyList<Client>()) }
    var products by remember { mutableStateOf(emptyList<Product>()) }
    var options by remember { mutableStateOf(OrderOptions(emptyList(), emptyList())) }
    var selectedClient by remember { mutableStateOf<Client?>(null) }
    var clientQuery by remember { mutableStateOf("") }
    var productQuery by remember { mutableStateOf("") }
    var paymentId by remember { mutableStateOf<Long?>(null) }
    var conditionId by remember { mutableStateOf<Long?>(null) }
    var priority by remember { mutableStateOf("normal") }
    var deliveryDate by remember { mutableStateOf("") }
    var observation by remember { mutableStateOf("") }
    var draftRecovered by remember { mutableStateOf(false) }
    val cart = remember { mutableStateMapOf<Long, String>() }
    val cartProducts = remember { mutableStateMapOf<Long, Product>() }

    fun currentDraft(): SavedOrderDraft? {
        val client = selectedClient ?: return null
        if (cart.isEmpty()) return null
        val lines = cart.mapNotNull { (productId, quantity) ->
            cartProducts[productId]?.let { OrderLineDraft(it, quantity) }
        }
        if (lines.size != cart.size) return null
        return SavedOrderDraft(
            client = client,
            paymentId = paymentId,
            conditionId = conditionId,
            priority = priority,
            deliveryDate = deliveryDate,
            observation = observation,
            lines = lines,
        )
    }

    suspend fun loadInitial() {
        loading = true
        error = null
        runCatching {
            clients = repository.clients(session)
            options = repository.orderOptions(session)
            val saved = repository.savedOrderDraft(session)
            if (saved == null) {
                paymentId = options.payments.firstOrNull()?.id
            } else {
                selectedClient = saved.client
                paymentId = saved.paymentId ?: options.payments.firstOrNull()?.id
                conditionId = saved.conditionId
                priority = saved.priority
                deliveryDate = saved.deliveryDate
                observation = saved.observation
                saved.lines.forEach { line ->
                    cart[line.product.id] = line.quantity
                    cartProducts[line.product.id] = line.product
                }
                val currentProducts = runCatching {
                    repository.products(session, saved.client.id)
                }.getOrDefault(emptyList())
                products = (currentProducts + saved.lines.map { it.product }).distinctBy { it.id }
                draftRecovered = true
            }
        }.onFailure { error = it.message ?: "Não foi possível carregar os dados." }
        loading = false
    }

    suspend fun selectClient(client: Client) {
        selectedClient = client
        cart.clear()
        cartProducts.clear()
        products = emptyList()
        loading = true
        error = null
        runCatching { products = repository.products(session, client.id) }
            .onFailure { error = it.message ?: "Não foi possível carregar os produtos." }
        loading = false
    }

    LaunchedEffect(Unit) { loadInitial() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Novo pedido") },
                navigationIcon = {
                    IconButton(onClick = {
                        currentDraft()?.let { repository.saveOrderDraft(session, it) }
                        onBack()
                    }) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Voltar")
                    }
                },
            )
        },
    ) { insets ->
        LazyColumn(
            modifier = Modifier
                .fillMaxSize()
                .padding(insets),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (loading) {
                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.Center,
                    ) { CircularProgressIndicator() }
                }
            }
            if (!error.isNullOrBlank()) {
                item { Text(error!!, color = MaterialTheme.colorScheme.error) }
            }
            if (draftRecovered) {
                item {
                    Text(
                        "Rascunho recuperado deste aparelho.",
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }

            item { SectionTitle("1. Cliente") }
            item {
                OutlinedTextField(
                    value = clientQuery,
                    onValueChange = { clientQuery = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("ID, nome, CPF/CNPJ ou celular") },
                    trailingIcon = {
                        IconButton(onClick = {
                            scope.launch {
                                loading = true
                                runCatching { clients = repository.clients(session, clientQuery) }
                                    .onFailure { error = it.message }
                                loading = false
                            }
                        }) { Icon(Icons.Outlined.Search, contentDescription = "Buscar") }
                    },
                    singleLine = true,
                )
            }
            selectedClient?.let { client ->
                item {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(14.dp)) {
                            Text(client.nome, fontWeight = FontWeight.Bold)
                            Text("${client.documento} • ${client.cidadeUf}")
                            Text(
                                "Crédito disponível: R$ ${client.creditoDisponivel} • ${client.tabelaPreco}",
                                style = MaterialTheme.typography.bodySmall,
                            )
                            OutlinedButton(onClick = {
                                repository.clearOrderDraft(session)
                                selectedClient = null
                                products = emptyList()
                                cart.clear()
                                cartProducts.clear()
                                draftRecovered = false
                            }) { Text("Trocar cliente") }
                        }
                    }
                }
            } ?: run {
                items(clients) { client ->
                    Card(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable(enabled = !client.bloqueado) {
                                scope.launch { selectClient(client) }
                            },
                    ) {
                        Column(Modifier.padding(14.dp)) {
                            Text(client.nome, fontWeight = FontWeight.Bold)
                            Text("${client.documento} • ${client.cidadeUf}")
                            Text(
                                if (client.bloqueado) {
                                    "Bloqueado: ${client.motivoBloqueio}"
                                } else {
                                    "Crédito disponível: R$ ${client.creditoDisponivel}"
                                },
                                color = if (client.bloqueado) {
                                    MaterialTheme.colorScheme.error
                                } else {
                                    MaterialTheme.colorScheme.onSurfaceVariant
                                },
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
            }

            if (selectedClient != null) {
                item { SectionTitle("2. Produtos") }
                item {
                    OutlinedTextField(
                        value = productQuery,
                        onValueChange = { productQuery = it },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("ID, código, código de barras ou nome") },
                        trailingIcon = {
                            IconButton(onClick = {
                                val client = selectedClient ?: return@IconButton
                                scope.launch {
                                    loading = true
                                    runCatching {
                                        products = repository.products(session, client.id, productQuery)
                                    }.onFailure { error = it.message }
                                    loading = false
                                }
                            }) { Icon(Icons.Outlined.Search, contentDescription = "Buscar") }
                        },
                        singleLine = true,
                    )
                }
                items(products) { product ->
                    ProductCard(
                        product = product,
                        inCart = cart.containsKey(product.id),
                        onAdd = {
                            if (product.podeVender) {
                                cart[product.id] = "1.000"
                                cartProducts[product.id] = product
                            }
                        },
                    )
                }

                if (cart.isNotEmpty()) {
                    item { SectionTitle("3. Itens do pedido") }
                    items(cart.keys.toList()) { productId ->
                        val product = cartProducts[productId]
                        if (product != null) {
                            Card(modifier = Modifier.fillMaxWidth()) {
                                Column(
                                    modifier = Modifier.padding(14.dp),
                                    verticalArrangement = Arrangement.spacedBy(8.dp),
                                ) {
                                    Text(product.descricao, fontWeight = FontWeight.Bold)
                                    Text("R$ ${product.preco} / ${product.unidade}")
                                    OutlinedTextField(
                                        value = cart[productId].orEmpty(),
                                        onValueChange = { cart[productId] = it.replace(',', '.') },
                                        modifier = Modifier.fillMaxWidth(),
                                        label = { Text("Quantidade") },
                                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                                        singleLine = true,
                                    )
                                    OutlinedButton(onClick = {
                                        cart.remove(productId)
                                        cartProducts.remove(productId)
                                        if (cart.isEmpty()) {
                                            repository.clearOrderDraft(session)
                                            draftRecovered = false
                                        }
                                    }) {
                                        Text("Remover")
                                    }
                                }
                            }
                        }
                    }

                    item { SectionTitle("4. Condições comerciais") }
                    item {
                        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("Forma de pagamento", fontWeight = FontWeight.SemiBold)
                            options.payments.forEach { payment ->
                                FilterChip(
                                    selected = paymentId == payment.id,
                                    onClick = { paymentId = payment.id },
                                    label = { Text(payment.descricao) },
                                )
                            }
                            if (options.conditions.isNotEmpty()) {
                                Text("Condição de pagamento", fontWeight = FontWeight.SemiBold)
                                options.conditions.forEach { condition ->
                                    FilterChip(
                                        selected = conditionId == condition.id,
                                        onClick = {
                                            conditionId = if (conditionId == condition.id) null else condition.id
                                        },
                                        label = { Text(condition.descricao) },
                                    )
                                }
                            }
                            Text("Prioridade", fontWeight = FontWeight.SemiBold)
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                listOf("normal" to "Normal", "urgente" to "Urgente", "vip" to "VIP")
                                    .forEach { (code, label) ->
                                        FilterChip(
                                            selected = priority == code,
                                            onClick = { priority = code },
                                            label = { Text(label) },
                                        )
                                    }
                            }
                            OutlinedTextField(
                                value = deliveryDate,
                                onValueChange = { deliveryDate = it },
                                modifier = Modifier.fillMaxWidth(),
                                label = { Text("Entrega prevista (AAAA-MM-DD)") },
                                singleLine = true,
                            )
                            OutlinedTextField(
                                value = observation,
                                onValueChange = { observation = it },
                                modifier = Modifier.fillMaxWidth(),
                                label = { Text("Observação") },
                                minLines = 2,
                            )
                        }
                    }
                    item {
                        val total = cart.entries.fold(BigDecimal.ZERO) { sum, entry ->
                            val product = cartProducts[entry.key]
                            val price = product?.preco?.toBigDecimalOrNull() ?: BigDecimal.ZERO
                            val quantity = entry.value.toBigDecimalOrNull() ?: BigDecimal.ZERO
                            sum + price.multiply(quantity)
                        }
                        Text(
                            "Total estimado: R$ ${total.setScale(2, java.math.RoundingMode.HALF_UP)}",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                    item {
                        OutlinedButton(
                            modifier = Modifier.fillMaxWidth(),
                            onClick = {
                                val draft = currentDraft()
                                if (draft == null) {
                                    error = "Revise o cliente e os itens antes de salvar."
                                } else {
                                    repository.saveOrderDraft(session, draft)
                                    draftRecovered = true
                                    error = null
                                }
                            },
                        ) {
                            Text("Salvar rascunho neste aparelho")
                        }
                    }
                    item {
                        Button(
                            modifier = Modifier.fillMaxWidth(),
                            enabled = !submitting && paymentId != null,
                            onClick = {
                                val client = selectedClient ?: return@Button
                                val lines = cart.mapNotNull { (id, quantity) ->
                                    cartProducts[id]
                                        ?.takeIf { (quantity.toBigDecimalOrNull() ?: BigDecimal.ZERO) > BigDecimal.ZERO }
                                        ?.let { OrderLineDraft(it, quantity) }
                                }
                                if (lines.size != cart.size) {
                                    error = "Revise as quantidades dos itens."
                                    return@Button
                                }
                                scope.launch {
                                    submitting = true
                                    error = null
                                    runCatching {
                                        repository.submitOrder(
                                            session,
                                            OrderDraft(
                                                client = client,
                                                paymentId = paymentId!!,
                                                conditionId = conditionId,
                                                prazoDias = client.prazoPagamentoDias,
                                                prioridade = priority,
                                                dataEntrega = deliveryDate.takeIf(String::isNotBlank),
                                                observacao = observation,
                                                lines = lines,
                                            ),
                                        )
                                    }.onSuccess { result ->
                                        repository.clearOrderDraft(session)
                                        if (result.queued) {
                                            onSubmitted(
                                                "Pedido salvo no aparelho. Use Sincronizar para enviá-lo ao ITED.",
                                            )
                                        } else {
                                            onSubmitted("Pedido ${result.officialNumber} recebido pelo ITED.")
                                        }
                                    }.onFailure {
                                        error = it.message ?: "Não foi possível enviar o pedido."
                                    }
                                    submitting = false
                                }
                            },
                        ) {
                            if (submitting) CircularProgressIndicator(strokeWidth = 2.dp)
                            else Text("Enviar pedido")
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
}

@Composable
private fun ProductCard(product: Product, inCart: Boolean, onAdd: () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(product.descricao, fontWeight = FontWeight.Bold)
            Text("${product.codigo} • ${product.unidade}")
            Text("R$ ${product.preco} • disponível ${product.estoqueDisponivel}")
            if (product.controlaLote) {
                Text("Controlado por lote (FEFO)", style = MaterialTheme.typography.labelSmall)
            }
            product.bloqueios.forEach {
                Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
            }
            Button(
                modifier = Modifier.fillMaxWidth(),
                enabled = product.podeVender && !inCart,
                onClick = onAdd,
            ) {
                Icon(Icons.Outlined.AddShoppingCart, contentDescription = null)
                Text(if (inCart) "Adicionado" else "Adicionar", modifier = Modifier.padding(start = 8.dp))
            }
        }
    }
}
