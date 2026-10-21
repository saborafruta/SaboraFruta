package br.com.ited.forcavendas.feature.separation

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
import androidx.compose.material.icons.outlined.Inventory2
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.BatchAllocation
import br.com.ited.forcavendas.data.SeparationInput
import br.com.ited.forcavendas.data.SeparationLine
import br.com.ited.forcavendas.data.SeparationOrder
import br.com.ited.forcavendas.data.UserSession
import java.math.BigDecimal
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SeparationScreen(
    repository: ItedRepository,
    session: UserSession,
    onBack: () -> Unit,
    onCompleted: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var orders by remember { mutableStateOf(emptyList<SeparationOrder>()) }
    var loading by remember { mutableStateOf(true) }
    var submitting by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var selectedOrderId by remember { mutableStateOf<Long?>(null) }
    var generalObservation by remember { mutableStateOf("") }
    val quantities = remember { mutableStateMapOf<Long, String>() }
    val deposits = remember { mutableStateMapOf<Long, Long>() }
    val manualLots = remember { mutableStateMapOf<Long, Boolean>() }
    val batchQuantities = remember { mutableStateMapOf<String, String>() }
    val observations = remember { mutableStateMapOf<Long, String>() }

    suspend fun reload() {
        loading = true
        error = null
        runCatching { orders = repository.separationQueue(session) }
            .onFailure { error = it.message ?: "Não foi possível carregar a separação." }
        loading = false
    }

    fun selectOrder(order: SeparationOrder) {
        selectedOrderId = if (selectedOrderId == order.id) null else order.id
        quantities.clear()
        deposits.clear()
        manualLots.clear()
        batchQuantities.clear()
        observations.clear()
        order.lines.forEach { line ->
            quantities[line.id] = line.quantidadeSolicitada
            val defaultDeposit = line.depositos.firstOrNull { it.padrao }
                ?: line.depositos.firstOrNull()
            if (defaultDeposit != null) deposits[line.id] = defaultDeposit.id
        }
    }

    LaunchedEffect(Unit) { reload() }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Separação de pedidos") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
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
            item {
                Text(
                    "Aguardando conferência",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    "A conclusão exige internet e revalida o saldo no ITED.",
                    style = MaterialTheme.typography.bodySmall,
                )
            }
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
            if (!loading && orders.isEmpty()) {
                item {
                    Card(modifier = Modifier.fillMaxWidth()) {
                        Column(
                            modifier = Modifier.padding(20.dp),
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            Icon(Icons.Outlined.Inventory2, contentDescription = null)
                            Text("Nenhum pedido aguardando separação")
                        }
                    }
                }
            }
            items(orders) { order ->
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clickable { selectOrder(order) },
                ) {
                    Column(
                        modifier = Modifier.padding(14.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            Text(order.numero, fontWeight = FontWeight.Bold)
                            Text(
                                if (order.status == "confirmado") "A separar" else "Separado",
                                color = MaterialTheme.colorScheme.primary,
                            )
                        }
                        Text(order.cliente)
                        Text(
                            listOfNotNull(
                                order.prioridade.uppercase(),
                                order.dataEntrega?.let { "Entrega $it" },
                            ).joinToString(" • "),
                            style = MaterialTheme.typography.bodySmall,
                        )
                        if (selectedOrderId == order.id) {
                            order.lines.forEach { line ->
                                SeparationLineEditor(
                                    line = line,
                                    quantity = quantities[line.id].orEmpty(),
                                    selectedDeposit = deposits[line.id],
                                    manualLots = manualLots[line.id] == true,
                                    batchQuantities = line.lotesSugeridos.associate { lote ->
                                        lote.id to batchQuantities[batchKey(line.id, lote.id)].orEmpty()
                                    },
                                    observation = observations[line.id].orEmpty(),
                                    onQuantityChange = { quantities[line.id] = it.replace(',', '.') },
                                    onDepositChange = { deposits[line.id] = it },
                                    onManualLotsChange = { manualLots[line.id] = it },
                                    onBatchQuantityChange = { batchId, value ->
                                        batchQuantities[batchKey(line.id, batchId)] =
                                            value.replace(',', '.')
                                    },
                                    onObservationChange = { observations[line.id] = it },
                                )
                            }
                            if (order.status == "confirmado") {
                                OutlinedTextField(
                                    value = generalObservation,
                                    onValueChange = { generalObservation = it },
                                    modifier = Modifier.fillMaxWidth(),
                                    label = { Text("Observação geral") },
                                    minLines = 2,
                                )
                                Button(
                                    modifier = Modifier.fillMaxWidth(),
                                    enabled = !submitting,
                                    onClick = {
                                        val inputs = mutableListOf<SeparationInput>()
                                        for (line in order.lines) {
                                            val depositId = deposits[line.id]
                                            val quantity = quantities[line.id]
                                            val decimal = quantity?.toBigDecimalOrNull()
                                            if (depositId == null || decimal == null || decimal < BigDecimal.ZERO) {
                                                error = "Revise depósito e quantidade do item ${line.numeroItem}."
                                                return@Button
                                            }
                                            val allocations = if (
                                                line.controlaLote && manualLots[line.id] == true
                                            ) {
                                                line.lotesSugeridos.mapNotNull { lote ->
                                                    val value = batchQuantities[
                                                        batchKey(line.id, lote.id)
                                                    ].orEmpty()
                                                    val allocated = value.toBigDecimalOrNull()
                                                    when {
                                                        value.isBlank() || allocated == BigDecimal.ZERO -> null
                                                        allocated == null || allocated < BigDecimal.ZERO -> {
                                                            error = "Quantidade inválida no lote ${lote.numero}."
                                                            return@Button
                                                        }
                                                        allocated > lote.quantidade.toBigDecimal() -> {
                                                            error = "O lote ${lote.numero} não possui saldo suficiente."
                                                            return@Button
                                                        }
                                                        else -> BatchAllocation(lote.id, value)
                                                    }
                                                }
                                            } else {
                                                emptyList()
                                            }
                                            if (
                                                line.controlaLote &&
                                                manualLots[line.id] == true &&
                                                decimal > BigDecimal.ZERO &&
                                                allocations.sumOf { it.quantity.toBigDecimal() } != decimal
                                            ) {
                                                error = "A soma dos lotes do item ${line.numeroItem} deve ser $quantity."
                                                return@Button
                                            }
                                            inputs += SeparationInput(
                                                itemId = line.id,
                                                depositoId = depositId,
                                                quantidade = quantity,
                                                batchAllocations = allocations,
                                                observacao = observations[line.id].orEmpty(),
                                            )
                                        }
                                        scope.launch {
                                            submitting = true
                                            error = null
                                            runCatching {
                                                repository.completeSeparation(
                                                    session,
                                                    order,
                                                    inputs,
                                                    generalObservation,
                                                )
                                            }.onSuccess { number ->
                                                onCompleted("Separação $number concluída e confirmada no ITED.")
                                            }.onFailure {
                                                error = it.message ?: "Não foi possível concluir a separação."
                                            }
                                            submitting = false
                                        }
                                    },
                                ) {
                                    if (submitting) CircularProgressIndicator(strokeWidth = 2.dp)
                                    else Text("Concluir separação")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SeparationLineEditor(
    line: SeparationLine,
    quantity: String,
    selectedDeposit: Long?,
    manualLots: Boolean,
    batchQuantities: Map<Long, String>,
    observation: String,
    onQuantityChange: (String) -> Unit,
    onDepositChange: (Long) -> Unit,
    onManualLotsChange: (Boolean) -> Unit,
    onBatchQuantityChange: (Long, String) -> Unit,
    onObservationChange: (String) -> Unit,
) {
    Card(modifier = Modifier.padding(top = 10.dp)) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("${line.numeroItem}. ${line.produtoDescricao}", fontWeight = FontWeight.Bold)
            Text("${line.produtoCodigo} • solicitado ${line.quantidadeSolicitada} ${line.unidade}")
            Text("Estoque de retirada", fontWeight = FontWeight.SemiBold)
            line.depositos.forEach { deposito ->
                FilterChip(
                    selected = selectedDeposit == deposito.id,
                    onClick = { onDepositChange(deposito.id) },
                    label = { Text("${deposito.nome}: ${deposito.disponivel}") },
                )
            }
            val depositoAtual = line.depositos.firstOrNull { it.id == selectedDeposit }
            Text("Onde buscar", fontWeight = FontWeight.SemiBold)
            if (depositoAtual?.localizacoes.isNullOrEmpty()) {
                Text(
                    "Saldo ainda sem endereço cadastrado.",
                    color = MaterialTheme.colorScheme.tertiary,
                    style = MaterialTheme.typography.bodySmall,
                )
            } else {
                depositoAtual.localizacoes.forEach { local ->
                    Text(
                        buildString {
                            append(local.codigo)
                            if (local.descricao.isNotBlank()) append(" · ${local.descricao}")
                            append(" — ${local.quantidade} ${line.unidade}")
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
            }
            if (line.controlaLote) {
                Text("Lote de retirada", fontWeight = FontWeight.SemiBold)
                if (line.lotesSugeridos.isEmpty()) {
                    Text("Nenhum lote vigente disponível", color = MaterialTheme.colorScheme.error)
                } else {
                    FilterChip(
                        selected = !manualLots,
                        onClick = { onManualLotsChange(false) },
                        label = { Text("Automático por FEFO") },
                    )
                    FilterChip(
                        selected = manualLots,
                        onClick = { onManualLotsChange(true) },
                        label = { Text("Dividir/selecionar lotes") },
                    )
                    if (manualLots) {
                        line.lotesSugeridos.forEach { lote ->
                            OutlinedTextField(
                                value = batchQuantities[lote.id].orEmpty(),
                                onValueChange = { onBatchQuantityChange(lote.id, it) },
                                modifier = Modifier.fillMaxWidth(),
                                label = { Text("Lote ${lote.numero} — disponível ${lote.quantidade}") },
                                supportingText = {
                                    Text(lote.validade?.let { "Validade $it" } ?: "Sem validade informada")
                                },
                                keyboardOptions = KeyboardOptions(
                                    keyboardType = KeyboardType.Decimal,
                                ),
                                singleLine = true,
                            )
                        }
                        Text(
                            "A soma informada nos lotes deve ser igual à quantidade separada.",
                            style = MaterialTheme.typography.bodySmall,
                        )
                    }
                }
            } else {
                Text("Produto sem controle de lote", style = MaterialTheme.typography.bodySmall)
            }
            OutlinedTextField(
                value = quantity,
                onValueChange = onQuantityChange,
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Quantidade separada") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Decimal),
                singleLine = true,
            )
            OutlinedTextField(
                value = observation,
                onValueChange = onObservationChange,
                modifier = Modifier.fillMaxWidth(),
                label = { Text("Divergência/observação") },
                singleLine = true,
            )
        }
    }
}

private fun batchKey(lineId: Long, batchId: Long): String = "$lineId:$batchId"
