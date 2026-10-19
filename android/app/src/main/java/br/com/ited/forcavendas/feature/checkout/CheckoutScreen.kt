package br.com.ited.forcavendas.feature.checkout

import androidx.compose.foundation.clickable
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.LocalOffer
import androidx.compose.material.icons.outlined.Payments
import androidx.compose.material.icons.outlined.PersonOutline
import androidx.compose.material.icons.outlined.Remove
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Schedule
import androidx.compose.material.icons.outlined.HourglassTop
import androidx.compose.material.icons.outlined.ShoppingCartCheckout
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilledTonalIconButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import br.com.ited.forcavendas.data.CheckoutLine
import br.com.ited.forcavendas.data.CheckoutPendingDraft
import br.com.ited.forcavendas.data.CheckoutSale
import br.com.ited.forcavendas.data.CheckoutState
import br.com.ited.forcavendas.data.Client
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.PaymentOption
import br.com.ited.forcavendas.data.Product
import br.com.ited.forcavendas.data.UserSession
import java.math.BigDecimal
import java.math.RoundingMode
import java.text.NumberFormat
import java.util.Locale
import kotlinx.coroutines.launch

private enum class DiscountInputMode { VALUE, PERCENT }

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CheckoutScreen(
    repository: ItedRepository,
    session: UserSession,
    initialPending: CheckoutPendingDraft? = null,
    onBack: () -> Unit,
    onCompleted: (String) -> Unit,
    onPendingSaved: (String) -> Unit,
    onOpenPendings: () -> Unit,
    onMessage: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var state by remember { mutableStateOf<CheckoutState?>(null) }
    var loading by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var client by remember(initialPending?.id) { mutableStateOf(initialPending?.client) }
    var clientQuery by remember { mutableStateOf("") }
    var clientResults by remember { mutableStateOf<List<Client>>(emptyList()) }
    var clientSearching by remember { mutableStateOf(false) }
    var quantity by remember { mutableStateOf("1") }
    var productQuery by remember { mutableStateOf("") }
    var productResults by remember { mutableStateOf<List<Product>>(emptyList()) }
    var productSearching by remember { mutableStateOf(false) }
    val productResultsScrollState = rememberScrollState()
    var lines by remember(initialPending?.id) { mutableStateOf(initialPending?.lines.orEmpty()) }
    var discount by remember(initialPending?.id) { mutableStateOf(initialPending?.discount ?: "0.00") }
    var selectedPayment by remember { mutableStateOf<PaymentOption?>(null) }
    var received by remember { mutableStateOf("") }
    var discountDialog by remember { mutableStateOf(false) }
    var discountAuthorizationDialog by remember { mutableStateOf(false) }
    var pendingAuthorizedDiscount by remember { mutableStateOf(BigDecimal.ZERO) }
    var discountAuthorization by remember(initialPending?.id) { mutableStateOf("") }
    var paymentDialog by remember { mutableStateOf(false) }
    var quickClientDialog by remember { mutableStateOf(false) }
    var editingQuantityLine by remember { mutableStateOf<CheckoutLine?>(null) }

    val subtotal = lines.fold(BigDecimal.ZERO) { total, line ->
        total + decimal(line.product.preco).multiply(decimal(line.quantity))
    }
    val total = (subtotal - decimal(discount)).max(BigDecimal.ZERO).setScale(2, RoundingMode.HALF_UP)
    val initialSubtotal = initialPending?.lines.orEmpty().fold(BigDecimal.ZERO) { sum, line ->
        sum + decimal(line.product.preco).multiply(decimal(line.quantity))
    }
    val currentDiscountRequiresAuthorization = discountRequiresAuthorization(
        subtotal = subtotal,
        discount = decimal(discount),
        maxPercent = decimal(state?.maxDiscountPercent.orEmpty()),
        maxRounding = decimal(state?.maxRoundingDiscount.orEmpty()),
    )
    val pendingKeepsAuthorization = initialPending != null &&
        decimal(discount) <= decimal(initialPending.discount) &&
        subtotal >= initialSubtotal

    fun loadState() {
        scope.launch {
            loading = true
            error = null
            runCatching { repository.checkoutState(session) }
                .onSuccess { loaded ->
                    state = loaded
                    if (selectedPayment == null && initialPending?.paymentId != null) {
                        selectedPayment = loaded.payments.firstOrNull { it.id == initialPending.paymentId }
                    }
                }
                .onFailure { error = it.message ?: "Não foi possível abrir o checkout." }
            loading = false
        }
    }

    LaunchedEffect(Unit) { loadState() }
    LaunchedEffect(productResults) {
        if (productResults.isNotEmpty()) productResultsScrollState.scrollTo(0)
    }
    LaunchedEffect(total, selectedPayment) {
        if (selectedPayment?.tipo != "dinheiro" || received.isBlank()) {
            received = total.toPlainString()
        }
    }

    fun searchClients() {
        val query = clientQuery.trim()
        if (query.length < 2 || clientSearching) return
        scope.launch {
            clientSearching = true
            runCatching { repository.checkoutClients(session, query) }
                .onSuccess {
                    clientResults = it
                    if (it.isEmpty()) onMessage("Nenhum cliente encontrado.")
                }
                .onFailure { onMessage(it.message ?: "Falha ao buscar clientes.") }
            clientSearching = false
        }
    }

    fun addProduct(product: Product) {
        val amount = decimal(quantity)
        if (amount <= BigDecimal.ZERO) {
            onMessage("Informe uma quantidade válida.")
            return
        }
        if (!product.podeVender) {
            onMessage(product.bloqueios.firstOrNull() ?: "Produto indisponível para venda.")
            return
        }
        val current = lines.firstOrNull { it.product.id == product.id }
        lines = if (current == null) {
            lines + CheckoutLine(product, amount.stripTrailingZeros().toPlainString())
        } else {
            lines.map { line ->
                if (line.product.id == product.id) {
                    line.copy(quantity = (decimal(line.quantity) + amount).stripTrailingZeros().toPlainString())
                } else line
            }
        }
        productResults = emptyList()
        productQuery = ""
        quantity = "1"
    }

    fun changeQuantity(line: CheckoutLine, delta: Int) {
        val next = decimal(line.quantity) + delta.toBigDecimal()
        lines = if (next <= BigDecimal.ZERO) {
            lines.filterNot { it.product.id == line.product.id }
        } else {
            lines.map { if (it.product.id == line.product.id) it.copy(quantity = next.toPlainString()) else it }
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Voltar")
                    }
                },
                title = {
                    Text(
                        "Checkout de venda",
                        fontWeight = FontWeight.Bold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                },
                actions = {
                    IconButton(onClick = onOpenPendings) {
                        Icon(Icons.Outlined.HourglassTop, contentDescription = "Vendas pendentes")
                    }
                    val open = state?.sessionOpen == true
                    Surface(
                        shape = CircleShape,
                        color = if (open) {
                            MaterialTheme.colorScheme.primaryContainer
                        } else {
                            MaterialTheme.colorScheme.errorContainer
                        },
                    ) {
                        Text(
                            if (open) "Caixa aberto" else "Caixa fechado",
                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 7.dp),
                            style = MaterialTheme.typography.labelMedium,
                            color = if (open) {
                                MaterialTheme.colorScheme.onPrimaryContainer
                            } else {
                                MaterialTheme.colorScheme.onErrorContainer
                            },
                        )
                    }
                    Spacer(Modifier.size(8.dp))
                },
            )
        },
        bottomBar = {
            if (!loading && state != null) {
                Surface(
                    modifier = Modifier.navigationBarsPadding(),
                    tonalElevation = 6.dp,
                ) {
                    Column(
                        modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.Bottom,
                        ) {
                            Text("TOTAL A PAGAR", style = MaterialTheme.typography.labelMedium)
                            Text(
                                money(total),
                                style = MaterialTheme.typography.headlineMedium,
                                fontWeight = FontWeight.Black,
                            )
                        }
                        if (initialPending == null) {
                            OutlinedButton(
                                modifier = Modifier.fillMaxWidth().height(50.dp),
                                enabled = !busy && lines.isNotEmpty(),
                                onClick = {
                                    scope.launch {
                                        busy = true
                                        runCatching {
                                            repository.saveCheckoutPending(
                                                session = session,
                                                client = client,
                                                lines = lines,
                                                discount = decimal(discount).setScale(2, RoundingMode.HALF_UP).toPlainString(),
                                                payment = selectedPayment,
                                                received = decimal(received).setScale(2, RoundingMode.HALF_UP).toPlainString(),
                                                discountAuthorization = discountAuthorization,
                                            )
                                        }.onSuccess { pending ->
                                            onPendingSaved("Venda #${pending.number} salva em pendências para o caixa.")
                                        }.onFailure {
                                            onMessage(it.message ?: "Não foi possível salvar a venda em pendências.")
                                        }
                                        busy = false
                                    }
                                },
                            ) {
                                Icon(Icons.Outlined.Schedule, contentDescription = null)
                                Spacer(Modifier.size(8.dp))
                                Text("Salvar em pendência", fontWeight = FontWeight.Bold)
                            }
                        }
                        Button(
                            modifier = Modifier.fillMaxWidth().height(54.dp),
                            enabled = !busy && state?.sessionOpen == true && lines.isNotEmpty() && selectedPayment != null,
                            onClick = {
                                val payment = selectedPayment ?: return@Button
                                if (decimal(received) < total) {
                                    onMessage("O valor recebido é menor que o total da venda.")
                                    return@Button
                                }
                                if (
                                    currentDiscountRequiresAuthorization &&
                                    discountAuthorization.isBlank() &&
                                    !pendingKeepsAuthorization
                                ) {
                                    pendingAuthorizedDiscount = decimal(discount)
                                    discountAuthorizationDialog = true
                                    onMessage("Autorize o desconto antes de finalizar a venda.")
                                    return@Button
                                }
                                scope.launch {
                                    busy = true
                                    runCatching {
                                        repository.submitCheckout(
                                            session,
                                            CheckoutSale(
                                                client = client,
                                                lines = lines,
                                                payment = payment,
                                                discount = decimal(discount).setScale(2, RoundingMode.HALF_UP).toPlainString(),
                                                received = decimal(received).setScale(2, RoundingMode.HALF_UP).toPlainString(),
                                                pendingId = initialPending?.id,
                                                discountAuthorization = discountAuthorization,
                                            ),
                                        )
                                    }.onSuccess { sale ->
                                        val change = decimal(sale.change)
                                        client = null
                                        clientQuery = ""
                                        clientResults = emptyList()
                                        quantity = "1"
                                        productQuery = ""
                                        productResults = emptyList()
                                        lines = emptyList()
                                        discount = "0.00"
                                        selectedPayment = null
                                        received = ""
                                        discountAuthorization = ""
                                        onCompleted(
                                            buildString {
                                                append("Venda #${sale.number} finalizada")
                                                if (change > BigDecimal.ZERO) append(" • Troco ${money(change)}")
                                                append(". Checkout pronto para a próxima venda.")
                                            },
                                        )
                                    }.onFailure {
                                        onMessage(it.message ?: "Não foi possível finalizar a venda.")
                                    }
                                    busy = false
                                }
                            },
                        ) {
                            if (busy) {
                                CircularProgressIndicator(
                                    modifier = Modifier.size(22.dp),
                                    strokeWidth = 2.5.dp,
                                )
                            } else {
                                Icon(Icons.Outlined.ShoppingCartCheckout, contentDescription = null)
                                Spacer(Modifier.size(8.dp))
                                Text("Finalizar venda", fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                }
            }
        },
    ) { insets ->
        when {
            loading -> Column(
                modifier = Modifier.fillMaxSize().padding(insets),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) { CircularProgressIndicator() }

            error != null -> Column(
                modifier = Modifier.fillMaxSize().padding(insets).padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Text(error.orEmpty(), color = MaterialTheme.colorScheme.error)
                Spacer(Modifier.height(12.dp))
                Button(onClick = ::loadState) { Text("Tentar novamente") }
            }

            else -> LazyColumn(
                modifier = Modifier.fillMaxSize().padding(insets),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                if (state?.sessionOpen == false) {
                    item {
                        Surface(
                            color = MaterialTheme.colorScheme.errorContainer,
                            shape = MaterialTheme.shapes.large,
                        ) {
                            Text(
                                "Abra o caixa no iTED antes de finalizar. Você pode montar o carrinho enquanto isso.",
                                modifier = Modifier.padding(14.dp),
                                color = MaterialTheme.colorScheme.onErrorContainer,
                            )
                        }
                    }
                }
                initialPending?.let { pending ->
                    item {
                        Surface(
                            color = MaterialTheme.colorScheme.primaryContainer,
                            shape = MaterialTheme.shapes.large,
                        ) {
                            Text(
                                "Venda #${pending.number} retomada. Revise a forma de pagamento e finalize para dar baixa.",
                                modifier = Modifier.padding(14.dp),
                                color = MaterialTheme.colorScheme.onPrimaryContainer,
                            )
                        }
                    }
                }
                item {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column {
                            Text("Cliente", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                        }
                        TextButton(onClick = { quickClientDialog = true }) { Text("Cadastro rápido") }
                    }
                }
                item {
                    Row(
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        OutlinedButton(
                            modifier = Modifier
                                .widthIn(min = 96.dp)
                                .height(56.dp),
                            contentPadding = PaddingValues(horizontal = 12.dp),
                            onClick = {
                                client = null
                                clientQuery = ""
                                clientResults = emptyList()
                            },
                        ) {
                            Icon(
                                Icons.Outlined.PersonOutline,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                            )
                            Spacer(Modifier.size(6.dp))
                            Text("Balcão")
                        }
                        OutlinedTextField(
                            modifier = Modifier.weight(1f),
                            value = client?.nome ?: clientQuery,
                            onValueChange = {
                                client = null
                                clientQuery = it
                            },
                            readOnly = client != null,
                            singleLine = true,
                            label = { Text("Cliente") },
                            placeholder = { Text("Nome ou CPF/CNPJ") },
                            trailingIcon = if (client != null) ({
                                IconButton(
                                    onClick = {
                                        client = null
                                        clientQuery = ""
                                        clientResults = emptyList()
                                    },
                                ) {
                                    Icon(Icons.Outlined.Close, contentDescription = "Limpar cliente")
                                }
                            }) else null,
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                            keyboardActions = KeyboardActions(onSearch = { searchClients() }),
                        )
                        FilledTonalIconButton(
                            modifier = Modifier.size(56.dp),
                            enabled = client == null && clientQuery.trim().length >= 2 && !clientSearching,
                            onClick = { searchClients() },
                        ) {
                            if (clientSearching) {
                                CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                            } else {
                                Icon(Icons.Outlined.Search, contentDescription = "Buscar cliente")
                            }
                        }
                    }
                }
                if (clientResults.isNotEmpty()) {
                    items(clientResults, key = { "client-${it.id}" }) { result ->
                        SearchResult(
                            title = result.nome,
                            subtitle = listOf(result.documento, result.cidadeUf).filter(String::isNotBlank).joinToString(" • "),
                            onClick = {
                                client = result
                                clientResults = emptyList()
                                clientQuery = ""
                            },
                        )
                    }
                }
                item { SectionTitle("Adicionar produto", "Bipe ou pesquise pelo código e nome") }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                        OutlinedTextField(
                            modifier = Modifier.weight(0.28f),
                            value = quantity,
                            onValueChange = { quantity = it.filter { char -> char.isDigit() || char == ',' || char == '.' } },
                            singleLine = true,
                            label = { Text("Qtd.") },
                            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                        )
                        OutlinedTextField(
                            modifier = Modifier.weight(0.72f),
                            value = productQuery,
                            onValueChange = { productQuery = it },
                            singleLine = true,
                            label = { Text("Código ou produto") },
                        )
                        Button(
                            enabled = productQuery.isNotBlank() && !productSearching,
                            contentPadding = PaddingValues(horizontal = 14.dp, vertical = 15.dp),
                            onClick = {
                                scope.launch {
                                    productSearching = true
                                    runCatching {
                                        repository.checkoutProducts(session, productQuery.trim(), client?.id)
                                    }.onSuccess {
                                        productResults = it
                                        if (it.isEmpty()) onMessage("Nenhum produto encontrado.")
                                    }.onFailure {
                                        onMessage(it.message ?: "Falha ao buscar produto.")
                                    }
                                    productSearching = false
                                }
                            },
                        ) {
                            if (productSearching) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                            else Icon(Icons.Outlined.Search, contentDescription = "Buscar")
                        }
                    }
                }
                if (productResults.isNotEmpty()) {
                    item {
                        Surface(
                            modifier = Modifier.fillMaxWidth(),
                            shape = MaterialTheme.shapes.large,
                            tonalElevation = 1.dp,
                        ) {
                            Column(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .heightIn(max = 328.dp)
                                    .verticalScroll(productResultsScrollState)
                                    .padding(8.dp),
                                verticalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                Text(
                                    "${productResults.size} resultado(s) • role somente esta lista",
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                productResults.forEach { product ->
                                    SearchResult(
                                        title = product.descricao,
                                        subtitle = "${product.codigo} • ${money(decimal(product.preco))} • Estoque ${product.estoqueDisponivel}",
                                        enabled = product.podeVender,
                                        onClick = { addProduct(product) },
                                    )
                                }
                            }
                        }
                    }
                }
                item { SectionTitle("Produtos da venda", "${lines.size} produto(s)") }
                if (lines.isEmpty()) {
                    item {
                        Surface(
                            modifier = Modifier.fillMaxWidth(),
                            color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = .55f),
                            shape = MaterialTheme.shapes.large,
                        ) {
                            Text(
                                "Bipe o primeiro produto para começar.",
                                modifier = Modifier.padding(28.dp),
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                } else {
                    items(lines, key = { "line-${it.product.id}" }) { line ->
                        CartLine(
                            line = line,
                            onMinus = { changeQuantity(line, -1) },
                            onPlus = { changeQuantity(line, 1) },
                            onEdit = { editingQuantityLine = line },
                            onRemove = { lines = lines.filterNot { it.product.id == line.product.id } },
                        )
                    }
                }
                item {
                    HorizontalDivider()
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(
                            modifier = Modifier.weight(0.9f),
                            enabled = state?.canApplyDiscount == true && subtotal > BigDecimal.ZERO,
                            onClick = { discountDialog = true },
                        ) {
                            Icon(Icons.Outlined.LocalOffer, contentDescription = null)
                            Spacer(Modifier.size(6.dp))
                            Text(if (decimal(discount) > BigDecimal.ZERO) "Desconto ${money(decimal(discount))}" else "Desconto")
                        }
                        OutlinedButton(
                            modifier = Modifier.weight(1.1f),
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 10.dp),
                            onClick = { paymentDialog = true },
                        ) {
                            Icon(
                                Icons.Outlined.Payments,
                                contentDescription = null,
                                modifier = Modifier.size(19.dp),
                            )
                            Spacer(Modifier.size(6.dp))
                            Text(
                                "Forma de pagamento",
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                                style = MaterialTheme.typography.labelMedium,
                            )
                        }
                    }
                    selectedPayment?.let { payment ->
                        Text(
                            "Selecionada: ${payment.descricao}",
                            modifier = Modifier.padding(top = 6.dp, start = 8.dp),
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                item {
                    OutlinedTextField(
                        modifier = Modifier.fillMaxWidth(),
                        value = received,
                        onValueChange = { received = it.filter { char -> char.isDigit() || char == ',' || char == '.' } },
                        label = { Text("Valor recebido") },
                        supportingText = {
                            if (decimal(received) > total) Text("Troco: ${money(decimal(received) - total)}")
                        },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    )
                }
                item { Spacer(Modifier.height(8.dp)) }
            }
        }
    }

    editingQuantityLine?.let { line ->
        var input by remember(line.product.id) { mutableStateOf(line.quantity) }
        AlertDialog(
            onDismissRequest = { editingQuantityLine = null },
            title = { Text("Alterar quantidade") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(line.product.descricao, fontWeight = FontWeight.SemiBold)
                    OutlinedTextField(
                        modifier = Modifier.fillMaxWidth(),
                        value = input,
                        onValueChange = {
                            input = it.filter { char -> char.isDigit() || char == ',' || char == '.' }
                        },
                        label = { Text("Quantidade") },
                        suffix = { Text(line.product.unidade) },
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Decimal,
                            imeAction = ImeAction.Done,
                        ),
                        singleLine = true,
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    val amount = decimal(input)
                    if (amount <= BigDecimal.ZERO) {
                        onMessage("Informe uma quantidade maior que zero.")
                        return@TextButton
                    }
                    lines = lines.map {
                        if (it.product.id == line.product.id) {
                            it.copy(quantity = amount.stripTrailingZeros().toPlainString())
                        } else it
                    }
                    editingQuantityLine = null
                }) { Text("Aplicar") }
            },
            dismissButton = {
                TextButton(onClick = { editingQuantityLine = null }) { Text("Cancelar") }
            },
        )
    }

    if (discountDialog) {
        var mode by remember(discountDialog) { mutableStateOf(DiscountInputMode.VALUE) }
        var input by remember(discountDialog) {
            mutableStateOf(decimal(discount).setScale(2, RoundingMode.HALF_UP).toPlainString())
        }
        val previewDiscount = discountFromInput(mode, input, subtotal)
            .min(subtotal)
            .setScale(2, RoundingMode.HALF_UP)
        val previewPercent = percentOf(subtotal, previewDiscount)
        val requiresAuthorization = discountRequiresAuthorization(
            subtotal = subtotal,
            discount = previewDiscount,
            maxPercent = decimal(state?.maxDiscountPercent.orEmpty()),
            maxRounding = decimal(state?.maxRoundingDiscount.orEmpty()),
        )
        AlertDialog(
            onDismissRequest = { discountDialog = false },
            title = { Text("Desconto da venda") },
            text = {
                Column(
                    modifier = Modifier.verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(
                            selected = mode == DiscountInputMode.VALUE,
                            onClick = {
                                mode = DiscountInputMode.VALUE
                                input = previewDiscount.toPlainString()
                            },
                            label = { Text("Valor (R$)") },
                        )
                        FilterChip(
                            selected = mode == DiscountInputMode.PERCENT,
                            onClick = {
                                mode = DiscountInputMode.PERCENT
                                input = previewPercent.stripTrailingZeros().toPlainString()
                            },
                            label = { Text("Porcentagem (%)") },
                        )
                    }
                    if (state?.discountPercentages.orEmpty().isNotEmpty()) {
                        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(
                                "Descontos rápidos",
                                style = MaterialTheme.typography.labelMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                state?.discountPercentages.orEmpty().forEach { percentage ->
                                    FilterChip(
                                        selected = mode == DiscountInputMode.PERCENT &&
                                            decimal(input) == decimal(percentage),
                                        onClick = {
                                            mode = DiscountInputMode.PERCENT
                                            input = decimal(percentage).stripTrailingZeros().toPlainString()
                                        },
                                        label = { Text("${decimal(percentage).stripTrailingZeros().toPlainString()}%") },
                                    )
                                }
                            }
                        }
                    }
                    OutlinedTextField(
                        modifier = Modifier.fillMaxWidth(),
                        value = input,
                        onValueChange = {
                            input = it.filter { char -> char.isDigit() || char == ',' || char == '.' }
                        },
                        label = {
                            Text(if (mode == DiscountInputMode.VALUE) "Valor do desconto" else "Percentual do desconto")
                        },
                        suffix = { Text(if (mode == DiscountInputMode.VALUE) "R$" else "%") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                    )
                    Text(
                        "${money(previewDiscount)} • ${previewPercent.setScale(2, RoundingMode.HALF_UP)}%",
                        fontWeight = FontWeight.Bold,
                    )
                    if (requiresAuthorization) {
                        Surface(
                            shape = MaterialTheme.shapes.medium,
                            color = MaterialTheme.colorScheme.errorContainer,
                        ) {
                            Text(
                                "Acima do limite de ${decimal(state?.maxDiscountPercent.orEmpty()).stripTrailingZeros().toPlainString()}%. Exige autorização.",
                                modifier = Modifier.padding(12.dp),
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    if (requiresAuthorization) {
                        pendingAuthorizedDiscount = previewDiscount
                        discountDialog = false
                        discountAuthorizationDialog = true
                        return@TextButton
                    }
                    discount = previewDiscount.toPlainString()
                    discountAuthorization = ""
                    discountDialog = false
                }) { Text("Aplicar") }
            },
            dismissButton = { TextButton(onClick = { discountDialog = false }) { Text("Cancelar") } },
        )
    }
    if (discountAuthorizationDialog) {
        var email by remember(discountAuthorizationDialog) { mutableStateOf("") }
        var password by remember(discountAuthorizationDialog) { mutableStateOf("") }
        var authorizing by remember(discountAuthorizationDialog) { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { if (!authorizing) discountAuthorizationDialog = false },
            title = { Text("Autorizar desconto") },
            text = {
                Column(
                    modifier = Modifier.verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Text(
                        "${money(pendingAuthorizedDiscount)} (${percentOf(subtotal, pendingAuthorizedDiscount).setScale(2, RoundingMode.HALF_UP)}%) ultrapassa a política da loja.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    OutlinedTextField(
                        modifier = Modifier.fillMaxWidth(),
                        value = email,
                        onValueChange = { email = it },
                        label = { Text("E-mail do aprovador") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
                    )
                    OutlinedTextField(
                        modifier = Modifier.fillMaxWidth(),
                        value = password,
                        onValueChange = { password = it },
                        label = { Text("Senha") },
                        singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    )
                    Text(
                        "Use um usuário com a permissão PDV → Aprovar.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            },
            confirmButton = {
                TextButton(
                    enabled = email.isNotBlank() && password.isNotBlank() && !authorizing,
                    onClick = {
                        scope.launch {
                            authorizing = true
                            runCatching {
                                repository.authorizeCheckoutDiscount(session, email, password)
                            }.onSuccess { token ->
                                discount = pendingAuthorizedDiscount
                                    .min(subtotal)
                                    .setScale(2, RoundingMode.HALF_UP)
                                    .toPlainString()
                                discountAuthorization = token
                                discountAuthorizationDialog = false
                                onMessage("Desconto autorizado e aplicado.")
                            }.onFailure {
                                onMessage(it.message ?: "Não foi possível autorizar o desconto.")
                            }
                            authorizing = false
                        }
                    },
                ) {
                    if (authorizing) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    else Text("Autorizar e aplicar")
                }
            },
            dismissButton = {
                TextButton(
                    enabled = !authorizing,
                    onClick = { discountAuthorizationDialog = false },
                ) { Text("Cancelar") }
            },
        )
    }
    if (quickClientDialog) {
        var name by remember(quickClientDialog) { mutableStateOf("") }
        var document by remember(quickClientDialog) { mutableStateOf("") }
        var phone by remember(quickClientDialog) { mutableStateOf("") }
        var saving by remember(quickClientDialog) { mutableStateOf(false) }
        AlertDialog(
            onDismissRequest = { if (!saving) quickClientDialog = false },
            title = { Text("Cadastro rápido") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    OutlinedTextField(
                        modifier = Modifier.fillMaxWidth(),
                        value = name,
                        onValueChange = { name = it },
                        label = { Text("Nome / Razão social") },
                        singleLine = true,
                    )
                    OutlinedTextField(
                        modifier = Modifier.fillMaxWidth(),
                        value = document,
                        onValueChange = { document = it },
                        label = { Text("CPF/CNPJ (opcional)") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                    )
                    OutlinedTextField(
                        modifier = Modifier.fillMaxWidth(),
                        value = phone,
                        onValueChange = { phone = it },
                        label = { Text("Celular (opcional)") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                    )
                }
            },
            confirmButton = {
                TextButton(
                    enabled = name.isNotBlank() && !saving,
                    onClick = {
                        scope.launch {
                            saving = true
                            runCatching {
                                repository.createCheckoutClient(session, name, document, phone)
                            }.onSuccess { created ->
                                client = created
                                clientQuery = ""
                                clientResults = emptyList()
                                quickClientDialog = false
                                onMessage("Cliente selecionado para esta venda.")
                            }.onFailure {
                                onMessage(it.message ?: "Não foi possível cadastrar o cliente.")
                            }
                            saving = false
                        }
                    },
                ) {
                    if (saving) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    else Text("Cadastrar")
                }
            },
            dismissButton = {
                TextButton(enabled = !saving, onClick = { quickClientDialog = false }) { Text("Cancelar") }
            },
        )
    }
    if (paymentDialog) {
        AlertDialog(
            onDismissRequest = { paymentDialog = false },
            title = { Text("Forma de pagamento") },
            text = {
                Column {
                    state?.payments.orEmpty().forEach { payment ->
                        Row(
                            modifier = Modifier.fillMaxWidth().clickable {
                                selectedPayment = payment
                                received = total.toPlainString()
                                paymentDialog = false
                            }.padding(vertical = 6.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(
                                selected = payment.id == selectedPayment?.id,
                                onClick = null,
                            )
                            Text(payment.descricao)
                        }
                    }
                }
            },
            confirmButton = {},
            dismissButton = { TextButton(onClick = { paymentDialog = false }) { Text("Fechar") } },
        )
    }
}

@Composable
private fun SectionTitle(title: String, subtitle: String) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Text(
            subtitle,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun SearchResult(
    title: String,
    subtitle: String,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    Surface(
        modifier = Modifier.fillMaxWidth().clickable(enabled = enabled, onClick = onClick),
        shape = MaterialTheme.shapes.medium,
        tonalElevation = 2.dp,
    ) {
        Column(Modifier.padding(13.dp)) {
            Text(title, fontWeight = FontWeight.SemiBold, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(
                subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = if (enabled) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.error,
            )
        }
    }
}

@Composable
private fun CartLine(
    line: CheckoutLine,
    onMinus: () -> Unit,
    onPlus: () -> Unit,
    onEdit: () -> Unit,
    onRemove: () -> Unit,
) {
    Surface(shape = MaterialTheme.shapes.large, tonalElevation = 1.dp) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                Column(Modifier.weight(1f)) {
                    Text(line.product.descricao, fontWeight = FontWeight.Bold, maxLines = 2)
                    Text(
                        "${line.product.codigo} • ${money(decimal(line.product.preco))}/${line.product.unidade}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                IconButton(onClick = onRemove) {
                    Icon(Icons.Outlined.DeleteOutline, contentDescription = "Remover", tint = MaterialTheme.colorScheme.error)
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onMinus) { Icon(Icons.Outlined.Remove, contentDescription = "Diminuir") }
                OutlinedButton(
                    onClick = onEdit,
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
                ) {
                    Text(line.quantity, fontWeight = FontWeight.Bold)
                }
                IconButton(onClick = onPlus) { Icon(Icons.Outlined.Add, contentDescription = "Aumentar") }
                Spacer(Modifier.weight(1f))
                Text(
                    money(decimal(line.product.preco) * decimal(line.quantity)),
                    fontWeight = FontWeight.Black,
                )
            }
        }
    }
}

private fun decimal(value: String): BigDecimal =
    value.trim().replace(',', '.').toBigDecimalOrNull() ?: BigDecimal.ZERO

private fun discountFromInput(
    mode: DiscountInputMode,
    input: String,
    subtotal: BigDecimal,
): BigDecimal = when (mode) {
    DiscountInputMode.VALUE -> decimal(input)
    DiscountInputMode.PERCENT -> subtotal
        .multiply(decimal(input))
        .divide(BigDecimal("100"), 2, RoundingMode.HALF_UP)
}

private fun percentOf(subtotal: BigDecimal, discount: BigDecimal): BigDecimal =
    if (subtotal <= BigDecimal.ZERO) BigDecimal.ZERO
    else discount.multiply(BigDecimal("100")).divide(subtotal, 4, RoundingMode.HALF_UP)

private fun discountRequiresAuthorization(
    subtotal: BigDecimal,
    discount: BigDecimal,
    maxPercent: BigDecimal,
    maxRounding: BigDecimal,
): Boolean {
    if (discount <= BigDecimal.ZERO) return false
    val percentageLimit = subtotal
        .multiply(maxPercent)
        .divide(BigDecimal("100"), 2, RoundingMode.HALF_UP)
    if (maxPercent > BigDecimal.ZERO && discount <= percentageLimit) return false
    val total = subtotal.subtract(discount)
    val excess = discount.subtract(percentageLimit).max(BigDecimal.ZERO)
    val integerTotal = total.stripTrailingZeros().scale() <= 0
    return !(maxRounding > BigDecimal.ZERO && integerTotal && excess <= maxRounding)
}

private fun money(value: BigDecimal): String = NumberFormat.getCurrencyInstance(
    Locale.forLanguageTag("pt-BR"),
).format(value)
