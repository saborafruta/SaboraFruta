package br.com.ited.forcavendas.feature.order

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.DeleteOutline
import androidx.compose.material.icons.outlined.EditNote
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.OrderSummary
import br.com.ited.forcavendas.data.SavedOrderDraft
import br.com.ited.forcavendas.data.UserSession
import java.math.BigDecimal
import java.math.RoundingMode
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrdersScreen(
    repository: ItedRepository,
    session: UserSession,
    onContinueDraft: () -> Unit,
    onBack: () -> Unit,
) {
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var orders by remember { mutableStateOf(emptyList<OrderSummary>()) }
    var draft by remember { mutableStateOf(repository.savedOrderDraft(session)) }
    LaunchedEffect(Unit) {
        runCatching { orders = repository.orders(session) }
            .onFailure { error = it.message ?: "Não foi possível carregar os pedidos." }
        loading = false
    }
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Meus pedidos") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Voltar")
                    }
                },
            )
        },
    ) { insets ->
        LazyColumn(
            modifier = Modifier.fillMaxSize().padding(insets),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            draft?.let { savedDraft ->
                item {
                    DraftCard(
                        draft = savedDraft,
                        onContinue = onContinueDraft,
                        onDelete = {
                            repository.clearOrderDraft(session)
                            draft = null
                        },
                    )
                }
            }
            if (loading) item {
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                    CircularProgressIndicator()
                }
            }
            error?.let { item { Text(it, color = MaterialTheme.colorScheme.error) } }
            if (!loading && orders.isEmpty()) item {
                Text("Nenhum pedido enviado encontrado.")
            }
            items(orders) { order ->
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(14.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            Text(order.numero, fontWeight = FontWeight.Bold)
                            Text(order.statusDescricao, color = MaterialTheme.colorScheme.primary)
                        }
                        Text(order.cliente)
                        Text("R$ ${order.valorTotal}", fontWeight = FontWeight.SemiBold)
                        Text(order.dataEmissao, style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
    }
}

@Composable
private fun DraftCard(
    draft: SavedOrderDraft,
    onContinue: () -> Unit,
    onDelete: () -> Unit,
) {
    val total = draft.lines.fold(BigDecimal.ZERO) { sum, line ->
        val price = line.product.preco.toBigDecimalOrNull() ?: BigDecimal.ZERO
        val quantity = line.quantity.toBigDecimalOrNull() ?: BigDecimal.ZERO
        sum + price.multiply(quantity)
    }.setScale(2, RoundingMode.HALF_UP)
    OutlinedCard(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Icon(
                        Icons.Outlined.EditNote,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.primary,
                    )
                    Column {
                        Text("Rascunho neste aparelho", fontWeight = FontWeight.Bold)
                        if (draft.savedAt.isNotBlank()) {
                            Text(
                                "Salvo em ${formatDraftDate(draft.savedAt)}",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
                Text(
                    "R$ $total",
                    fontWeight = FontWeight.Bold,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            Text(draft.client.nome, fontWeight = FontWeight.SemiBold)
            Text(
                "${draft.lines.size} produto(s) • ainda não enviado ao ITED",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Button(modifier = Modifier.weight(1f), onClick = onContinue) {
                    Text("Continuar pedido")
                }
                OutlinedButton(onClick = onDelete) {
                    Icon(Icons.Outlined.DeleteOutline, contentDescription = null)
                    Text("Excluir", modifier = Modifier.padding(start = 6.dp))
                }
            }
        }
    }
}

private fun formatDraftDate(raw: String): String = runCatching {
    DateTimeFormatter.ofPattern("dd/MM/yyyy HH:mm")
        .withZone(ZoneId.systemDefault())
        .format(Instant.parse(raw))
}.getOrDefault(raw)
