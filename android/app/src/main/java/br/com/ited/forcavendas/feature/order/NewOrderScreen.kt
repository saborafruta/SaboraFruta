package br.com.ited.forcavendas.feature.order

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.AddShoppingCart
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.StarOutline
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
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
import androidx.compose.ui.text.style.TextOverflow
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
import java.math.RoundingMode
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

private data class ProductFilter(
    val key: String,
    val label: String,
    val categoryId: Long? = null,
    val highlightsOnly: Boolean = false,
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NewOrderScreen(
    repository: ItedRepository,
    session: UserSession,
    onBack: () -> Unit,
    onSubmitted: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var initialLoading by remember { mutableStateOf(true) }
    var clientSearchLoading by remember { mutableStateOf(false) }
    var productSearchLoading by remember { mutableStateOf(false) }
    var submitting by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var clients by remember { mutableStateOf(emptyList<Client>()) }
    var products by remember { mutableStateOf(emptyList<Product>()) }
    var clientSearchExecuted by remember { mutableStateOf(false) }
    var productSearchExecuted by remember { mutableStateOf(false) }
    var clientResultsVisible by remember { mutableStateOf(false) }
    var productResultsVisible by remember { mutableStateOf(false) }
    var options by remember { mutableStateOf(OrderOptions(emptyList(), emptyList())) }
    var selectedClient by remember { mutableStateOf<Client?>(null) }
    var selectedProductFilter by remember { mutableStateOf<ProductFilter?>(null) }
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

    val productFilters = remember(options.categories) {
        listOf(
            ProductFilter("highlights", "Destaques", highlightsOnly = true),
            ProductFilter("all", "Todos"),
        ) + options.categories.map { category ->
            ProductFilter("category-${category.id}", category.nome, categoryId = category.id)
        }
    }

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

    fun selectClient(client: Client) {
        selectedClient = client
        clientQuery = ""
        clients = emptyList()
        clientResultsVisible = false
        clientSearchExecuted = false
        selectedProductFilter = null
        productQuery = ""
        products = emptyList()
        productResultsVisible = false
        productSearchExecuted = false
        cart.clear()
        cartProducts.clear()
        error = null
    }

    fun clearProductSearch() {
        products = emptyList()
        productResultsVisible = false
        productSearchExecuted = false
    }

    LaunchedEffect(Unit) {
        initialLoading = true
        error = null
        runCatching {
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
                draftRecovered = true
            }
        }.onFailure { error = it.message ?: "Não foi possível carregar os dados." }
        initialLoading = false
    }

    LaunchedEffect(clientQuery, selectedClient) {
        val query = clientQuery.trim()
        if (selectedClient != null || query.length < 2) {
            clients = emptyList()
            clientSearchExecuted = false
            clientSearchLoading = false
            if (query.length < 2) clientResultsVisible = false
            return@LaunchedEffect
        }
        delay(250)
        clientSearchLoading = true
        clientResultsVisible = true
        clientSearchExecuted = false
        runCatching { repository.clients(session, query) }
            .onSuccess { clients = it }
            .onFailure {
                clients = emptyList()
                error = it.message ?: "Não foi possível buscar clientes."
            }
        clientSearchExecuted = true
        clientSearchLoading = false
    }

    LaunchedEffect(productQuery, selectedProductFilter, selectedClient) {
        val client = selectedClient
        val query = productQuery.trim()
        val filter = selectedProductFilter
        if (client == null || (query.length < 2 && filter == null)) {
            clearProductSearch()
            productSearchLoading = false
            return@LaunchedEffect
        }
        if (query.length >= 2) delay(250)
        productSearchLoading = true
        productResultsVisible = true
        productSearchExecuted = false
        runCatching {
            repository.products(
                session = session,
                clientId = client.id,
                query = query,
                categoryId = filter?.categoryId,
                highlightsOnly = filter?.highlightsOnly == true,
            )
        }.onSuccess { products = it }
            .onFailure {
                products = emptyList()
                error = it.message ?: "Não foi possível buscar produtos."
            }
        productSearchExecuted = true
        productSearchLoading = false
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("Novo pedido", fontWeight = FontWeight.Bold)
                        Text(
                            "${cart.size} produto(s) selecionado(s)",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
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
            modifier = Modifier.fillMaxSize().padding(insets),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            if (initialLoading) {
                item {
                    Box(Modifier.fillMaxWidth().height(96.dp), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator()
                    }
                }
            }
            if (!error.isNullOrBlank()) item { ErrorMessage(error.orEmpty()) }
            if (draftRecovered) {
                item {
                    Surface(
                        shape = MaterialTheme.shapes.medium,
                        color = MaterialTheme.colorScheme.primaryContainer,
                    ) {
                        Text(
                            "Rascunho recuperado deste aparelho.",
                            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                            color = MaterialTheme.colorScheme.onPrimaryContainer,
                            style = MaterialTheme.typography.bodySmall,
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                }
            }

            item {
                SectionCard(step = "1", title = "Cliente", caption = "Obrigatório") {
                    val client = selectedClient
                    if (client == null) {
                        ExposedDropdownMenuBox(
                            expanded = clientResultsVisible &&
                                (clientSearchLoading || clientSearchExecuted),
                            onExpandedChange = { expanded ->
                                clientResultsVisible = expanded && clientQuery.trim().length >= 2
                            },
                        ) {
                            OutlinedTextField(
                                value = clientQuery,
                                onValueChange = {
                                    clientQuery = it
                                    clientResultsVisible = it.trim().length >= 2
                                    error = null
                                },
                                modifier = Modifier
                                    .menuAnchor()
                                    .fillMaxWidth(),
                                label = { Text("Buscar cliente") },
                                placeholder = { Text("Nome, CNPJ ou telefone") },
                                leadingIcon = { Icon(Icons.Outlined.Search, contentDescription = null) },
                                trailingIcon = {
                                    if (clientSearchLoading) {
                                        CircularProgressIndicator(
                                            modifier = Modifier.size(18.dp),
                                            strokeWidth = 2.dp,
                                        )
                                    } else {
                                        ExposedDropdownMenuDefaults.TrailingIcon(
                                            expanded = clientResultsVisible,
                                        )
                                    }
                                },
                                supportingText = {
                                    Text("Digite ao menos 2 caracteres. A lista fecha ao tocar fora.")
                                },
                                singleLine = true,
                            )
                            ExposedDropdownMenu(
                                expanded = clientResultsVisible &&
                                    (clientSearchLoading || clientSearchExecuted),
                                onDismissRequest = { clientResultsVisible = false },
                            ) {
                                if (clientSearchLoading) {
                                    SearchState("Buscando clientes…")
                                } else if (clients.isEmpty()) {
                                    SearchState("Nenhum cliente encontrado.")
                                } else {
                                    clients.forEach { result ->
                                        DropdownMenuItem(
                                            enabled = !result.bloqueado,
                                            text = {
                                                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                                                    Text(
                                                        result.nome,
                                                        fontWeight = FontWeight.SemiBold,
                                                        maxLines = 1,
                                                        overflow = TextOverflow.Ellipsis,
                                                    )
                                                    Text(
                                                        listOf(result.documento, result.cidadeUf)
                                                            .filter(String::isNotBlank)
                                                            .joinToString(" • "),
                                                        style = MaterialTheme.typography.labelSmall,
                                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                                    )
                                                    Text(
                                                        if (result.bloqueado) {
                                                            "Bloqueado: ${result.motivoBloqueio}"
                                                        } else result.tabelaPreco,
                                                        style = MaterialTheme.typography.labelSmall,
                                                        color = if (result.bloqueado) {
                                                            MaterialTheme.colorScheme.error
                                                        } else MaterialTheme.colorScheme.primary,
                                                    )
                                                }
                                            },
                                            onClick = { selectClient(result) },
                                        )
                                    }
                                }
                            }
                        }
                    } else {
                        SelectedClientCard(
                            client = client,
                            onChange = {
                                repository.clearOrderDraft(session)
                                selectedClient = null
                                selectedProductFilter = null
                                productQuery = ""
                                clearProductSearch()
                                cart.clear()
                                cartProducts.clear()
                                draftRecovered = false
                            },
                        )
                    }
                }
            }

            item {
                SectionCard(
                    step = "2",
                    title = "Produtos",
                    caption = if (selectedClient == null) "Selecione o cliente" else "${cart.size} no pedido",
                ) {
                    if (selectedClient == null) {
                        Text(
                            "A busca de produtos será liberada depois que você selecionar o cliente.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    } else {
                        ExposedDropdownMenuBox(
                            expanded = productResultsVisible &&
                                (productSearchLoading || productSearchExecuted),
                            onExpandedChange = { expanded ->
                                productResultsVisible = expanded &&
                                    (productQuery.trim().length >= 2 || selectedProductFilter != null)
                            },
                        ) {
                            OutlinedTextField(
                                value = productQuery,
                                onValueChange = {
                                    productQuery = it
                                    productResultsVisible = it.trim().length >= 2 ||
                                        selectedProductFilter != null
                                    error = null
                                },
                                modifier = Modifier
                                    .menuAnchor()
                                    .fillMaxWidth(),
                                label = { Text("Buscar produto") },
                                placeholder = { Text("Nome, código ou código de barras") },
                                leadingIcon = { Icon(Icons.Outlined.Search, contentDescription = null) },
                                trailingIcon = {
                                    if (productSearchLoading) {
                                        CircularProgressIndicator(
                                            modifier = Modifier.size(18.dp),
                                            strokeWidth = 2.dp,
                                        )
                                    } else {
                                        ExposedDropdownMenuDefaults.TrailingIcon(
                                            expanded = productResultsVisible,
                                        )
                                    }
                                },
                                singleLine = true,
                            )
                            ExposedDropdownMenu(
                                expanded = productResultsVisible &&
                                    (productSearchLoading || productSearchExecuted),
                                onDismissRequest = { productResultsVisible = false },
                            ) {
                                if (productSearchLoading) {
                                    SearchState("Buscando produtos…")
                                } else if (products.isEmpty()) {
                                    SearchState("Nenhum produto encontrado neste filtro.")
                                } else {
                                    products.forEachIndexed { index, product ->
                                        ProductResult(
                                            product = product,
                                            inCart = cart.containsKey(product.id),
                                            onAdd = {
                                                if (product.podeVender) {
                                                    val current = cart[product.id]
                                                        ?.toBigDecimalOrNull() ?: BigDecimal.ZERO
                                                    cart[product.id] = current.add(BigDecimal.ONE)
                                                        .setScale(3).toPlainString()
                                                    cartProducts[product.id] = product
                                                    productQuery = ""
                                                    selectedProductFilter = null
                                                    clearProductSearch()
                                                }
                                            },
                                        )
                                        if (index < products.lastIndex) HorizontalDivider()
                                    }
                                }
                            }
                        }

                        Spacer(Modifier.height(12.dp))
                        Text(
                            "Categorias",
                            style = MaterialTheme.typography.labelLarge,
                            fontWeight = FontWeight.SemiBold,
                        )
                        Spacer(Modifier.height(6.dp))
                        LazyRow(
                            horizontalArrangement = Arrangement.spacedBy(8.dp),
                            contentPadding = PaddingValues(end = 8.dp),
                        ) {
                            items(productFilters, key = { it.key }) { filter ->
                                FilterChip(
                                    selected = selectedProductFilter?.key == filter.key,
                                    onClick = {
                                        selectedProductFilter = if (
                                            selectedProductFilter?.key == filter.key
                                        ) null else filter
                                        productResultsVisible = selectedProductFilter != null ||
                                            productQuery.trim().length >= 2
                                        error = null
                                    },
                                    label = { Text(filter.label) },
                                    leadingIcon = if (filter.highlightsOnly) {
                                        {
                                            Icon(
                                                Icons.Outlined.StarOutline,
                                                contentDescription = null,
                                                modifier = Modifier.size(18.dp),
                                            )
                                        }
                                    } else null,
                                )
                            }
                        }
                        Text(
                            "Digite para pesquisar ou escolha uma categoria. Destaques reúne as sugestões comerciais.",
                            modifier = Modifier.padding(top = 6.dp),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            if (cart.isNotEmpty()) {
                item {
                    SectionCard(step = "3", title = "Itens do pedido", caption = "${cart.size} item(ns)") {
                        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            cart.keys.toList().forEach { productId ->
                                cartProducts[productId]?.let { product ->
                                    CartItemCard(
                                        product = product,
                                        quantity = cart[productId].orEmpty(),
                                        onQuantityChange = { cart[productId] = it.replace(',', '.') },
                                        onRemove = {
                                            cart.remove(productId)
                                            cartProducts.remove(productId)
                                            if (cart.isEmpty()) {
                                                repository.clearOrderDraft(session)
                                                draftRecovered = false
                                            }
                                        },
                                    )
                                }
                            }
                        }
                    }
                }

                item {
                    SectionCard(step = "4", title = "Condições comerciais") {
                        Text("Forma de pagamento", style = MaterialTheme.typography.labelLarge)
                        ChoiceRow(
                            choices = options.payments.map { it.id to it.descricao },
                            selectedId = paymentId,
                            onSelect = { paymentId = it },
                        )
                        if (options.conditions.isNotEmpty()) {
                            Spacer(Modifier.height(10.dp))
                            Text("Condição de pagamento", style = MaterialTheme.typography.labelLarge)
                            ChoiceRow(
                                choices = options.conditions.map { it.id to it.descricao },
                                selectedId = conditionId,
                                onSelect = { id -> conditionId = if (conditionId == id) null else id },
                            )
                        }
                        Spacer(Modifier.height(10.dp))
                        Text("Prioridade", style = MaterialTheme.typography.labelLarge)
                        LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            items(
                                listOf("normal" to "Normal", "urgente" to "Urgente", "vip" to "VIP"),
                                key = { it.first },
                            ) { (code, label) ->
                                FilterChip(
                                    selected = priority == code,
                                    onClick = { priority = code },
                                    label = { Text(label) },
                                )
                            }
                        }
                        Spacer(Modifier.height(10.dp))
                        OutlinedTextField(
                            value = deliveryDate,
                            onValueChange = { deliveryDate = it },
                            modifier = Modifier.fillMaxWidth(),
                            label = { Text("Entrega prevista") },
                            placeholder = { Text("AAAA-MM-DD") },
                            singleLine = true,
                        )
                        Spacer(Modifier.height(10.dp))
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
                    Surface(
                        shape = MaterialTheme.shapes.large,
                        color = MaterialTheme.colorScheme.primaryContainer,
                    ) {
                        Row(
                            modifier = Modifier.padding(16.dp).fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column {
                                Text(
                                    "Total estimado",
                                    style = MaterialTheme.typography.labelMedium,
                                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                                )
                                Text(
                                    "${cart.size} produto(s)",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onPrimaryContainer,
                                )
                            }
                            Text(
                                "R$ ${total.setScale(2, RoundingMode.HALF_UP)}",
                                style = MaterialTheme.typography.titleLarge,
                                fontWeight = FontWeight.ExtraBold,
                                color = MaterialTheme.colorScheme.onPrimaryContainer,
                            )
                        }
                    }
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
                    ) { Text("Salvar rascunho neste aparelho") }
                    Spacer(Modifier.height(8.dp))
                    Button(
                        modifier = Modifier.fillMaxWidth().height(50.dp),
                        enabled = !submitting && paymentId != null,
                        onClick = {
                            val client = selectedClient ?: return@Button
                            val lines = cart.mapNotNull { (id, quantity) ->
                                cartProducts[id]
                                    ?.takeIf {
                                        (quantity.toBigDecimalOrNull() ?: BigDecimal.ZERO) > BigDecimal.ZERO
                                    }
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
                                    } else onSubmitted("Pedido ${result.officialNumber} recebido pelo ITED.")
                                }.onFailure {
                                    error = it.message ?: "Não foi possível enviar o pedido."
                                }
                                submitting = false
                            }
                        },
                    ) {
                        if (submitting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(20.dp),
                                strokeWidth = 2.dp,
                            )
                        } else {
                            Icon(Icons.Outlined.AddShoppingCart, contentDescription = null)
                            Spacer(Modifier.width(8.dp))
                            Text("Enviar pedido")
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SectionCard(
    step: String,
    title: String,
    caption: String? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.extraLarge,
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainerLow,
        ),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Surface(
                    modifier = Modifier.size(30.dp),
                    shape = MaterialTheme.shapes.medium,
                    color = MaterialTheme.colorScheme.primaryContainer,
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Text(
                            step,
                            fontWeight = FontWeight.ExtraBold,
                            color = MaterialTheme.colorScheme.onPrimaryContainer,
                        )
                    }
                }
                Text(
                    title,
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                caption?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Spacer(Modifier.height(14.dp))
            content()
        }
    }
}

@Composable
private fun SelectedClientCard(client: Client, onChange: () -> Unit) {
    OutlinedCard(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(5.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.Top,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        client.nome,
                        fontWeight = FontWeight.Bold,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        listOf(client.documento, client.cidadeUf)
                            .filter(String::isNotBlank).joinToString(" • "),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                OutlinedButton(onClick = onChange) { Text("Trocar") }
            }
            HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
            Text(
                "Crédito disponível: R$ ${client.creditoDisponivel}",
                style = MaterialTheme.typography.bodySmall,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                "Tabela: ${client.tabelaPreco}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun SearchState(message: String) {
    DropdownMenuItem(
        enabled = false,
        text = {
            Text(
                message,
                modifier = Modifier.fillMaxWidth(),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        },
        onClick = {},
    )
}

@Composable
private fun ProductResult(product: Product, inCart: Boolean, onAdd: () -> Unit) {
    DropdownMenuItem(
        enabled = product.podeVender,
        text = {
            Column(
                modifier = Modifier.widthIn(min = 220.dp),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        product.descricao,
                        modifier = Modifier.weight(1f),
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    if (product.destaque) {
                        Icon(
                            Icons.Outlined.StarOutline,
                            contentDescription = "Destaque",
                            modifier = Modifier.size(16.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                    }
                }
                Text(
                    listOf(product.codigo, product.precoOrigem, product.categoria)
                        .filter(String::isNotBlank).joinToString(" • "),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    "Disponível ${product.estoqueDisponivel} ${product.unidade}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                product.bloqueios.firstOrNull()?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        },
        trailingIcon = {
            Text(
                if (inCart) "Adicionar +1" else "R$ ${product.preco}",
                style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
            )
        },
        onClick = onAdd,
    )
}

@Composable
private fun CartItemCard(
    product: Product,
    quantity: String,
    onQuantityChange: (String) -> Unit,
    onRemove: () -> Unit,
) {
    OutlinedCard(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.Top,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        product.descricao,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        "R$ ${product.preco} / ${product.unidade} • ${product.categoria}",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(onClick = onRemove) {
                    Icon(
                        Icons.Outlined.DeleteOutline,
                        contentDescription = "Remover produto",
                        tint = MaterialTheme.colorScheme.error,
                    )
                }
            }
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedTextField(
                    value = quantity,
                    onValueChange = onQuantityChange,
                    modifier = Modifier.width(132.dp),
                    label = { Text("Quantidade") },
                    suffix = { Text(product.unidade) },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    singleLine = true,
                )
                val lineTotal = (product.preco.toBigDecimalOrNull() ?: BigDecimal.ZERO)
                    .multiply(quantity.toBigDecimalOrNull() ?: BigDecimal.ZERO)
                    .setScale(2, RoundingMode.HALF_UP)
                Column(horizontalAlignment = Alignment.End, modifier = Modifier.weight(1f)) {
                    Text(
                        "Subtotal",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        "R$ $lineTotal",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                    )
                }
            }
        }
    }
}

@Composable
private fun ChoiceRow(
    choices: List<Pair<Long, String>>,
    selectedId: Long?,
    onSelect: (Long) -> Unit,
) {
    LazyRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = PaddingValues(end = 8.dp),
    ) {
        items(choices, key = { it.first }) { (id, label) ->
            FilterChip(
                selected = selectedId == id,
                onClick = { onSelect(id) },
                label = { Text(label) },
            )
        }
    }
}

@Composable
private fun ErrorMessage(message: String) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.medium,
        color = MaterialTheme.colorScheme.errorContainer,
    ) {
        Text(
            message,
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
            color = MaterialTheme.colorScheme.onErrorContainer,
            style = MaterialTheme.typography.bodySmall,
        )
    }
}
