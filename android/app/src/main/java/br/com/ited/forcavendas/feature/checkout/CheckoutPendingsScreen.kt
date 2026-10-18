package br.com.ited.forcavendas.feature.checkout

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.HourglassTop
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import br.com.ited.forcavendas.data.CheckoutPendingDraft
import br.com.ited.forcavendas.data.CheckoutPendingSummary
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.UserSession
import java.math.BigDecimal
import java.text.NumberFormat
import java.time.OffsetDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CheckoutPendingsScreen(
    repository: ItedRepository,
    session: UserSession,
    onBack: () -> Unit,
    onOpen: (CheckoutPendingDraft) -> Unit,
    onMessage: (String) -> Unit,
) {
    val scope = rememberCoroutineScope()
    var loading by remember { mutableStateOf(true) }
    var openingId by remember { mutableStateOf<Long?>(null) }
    var pendings by remember { mutableStateOf<List<CheckoutPendingSummary>>(emptyList()) }
    var error by remember { mutableStateOf<String?>(null) }

    fun load() {
        scope.launch {
            loading = true
            error = null
            runCatching { repository.checkoutPendings(session) }
                .onSuccess { pendings = it }
                .onFailure { error = it.message ?: "Não foi possível carregar as pendências." }
            loading = false
        }
    }

    LaunchedEffect(Unit) { load() }

    Scaffold(
        topBar = {
            TopAppBar(
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Outlined.ArrowBack, contentDescription = "Voltar")
                    }
                },
                title = { Text("Vendas pendentes", fontWeight = FontWeight.Bold) },
                actions = {
                    IconButton(enabled = !loading, onClick = ::load) {
                        Icon(Icons.Outlined.Refresh, contentDescription = "Atualizar pendências")
                    }
                },
            )
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
                Spacer(Modifier.size(12.dp))
                Button(onClick = ::load) { Text("Tentar novamente") }
            }

            pendings.isEmpty() -> Column(
                modifier = Modifier.fillMaxSize().padding(insets).padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Icon(
                    Icons.Outlined.HourglassTop,
                    contentDescription = null,
                    modifier = Modifier.size(42.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.size(12.dp))
                Text("Nenhuma venda aguardando recebimento", fontWeight = FontWeight.Bold)
                Text(
                    "As vendas salvas no checkout aparecerão aqui para o caixa.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            else -> LazyColumn(
                modifier = Modifier.fillMaxSize().padding(insets),
                contentPadding = PaddingValues(16.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                item {
                    Text(
                        "Selecione uma venda para revisar o pagamento e dar baixa.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                items(pendings, key = { it.id }) { pending ->
                    Surface(
                        modifier = Modifier.fillMaxWidth().clickable(enabled = openingId == null) {
                            scope.launch {
                                openingId = pending.id
                                runCatching { repository.checkoutPendingDetail(session, pending.id) }
                                    .onSuccess(onOpen)
                                    .onFailure {
                                        onMessage(it.message ?: "Não foi possível abrir a venda pendente.")
                                    }
                                openingId = null
                            }
                        },
                        shape = MaterialTheme.shapes.large,
                        tonalElevation = 2.dp,
                    ) {
                        Row(
                            modifier = Modifier.padding(16.dp),
                            horizontalArrangement = Arrangement.spacedBy(12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Icon(
                                Icons.Outlined.HourglassTop,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.primary,
                            )
                            Column(Modifier.weight(1f)) {
                                Text(
                                    "Venda #${pending.number}",
                                    fontWeight = FontWeight.Bold,
                                )
                                Text(
                                    pending.clientName,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Text(
                                    "${pending.itemCount} item(ns) • ${formatDate(pending.createdAt)}",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            if (openingId == pending.id) {
                                CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                            } else {
                                Column(horizontalAlignment = Alignment.End) {
                                    Text(
                                        checkoutMoney(pending.total),
                                        fontWeight = FontWeight.Black,
                                        color = MaterialTheme.colorScheme.primary,
                                    )
                                    if ((pending.discount.toBigDecimalOrNull() ?: BigDecimal.ZERO) > BigDecimal.ZERO) {
                                        Text(
                                            "Desc. ${checkoutMoney(pending.discount)}",
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun formatDate(raw: String): String = runCatching {
    OffsetDateTime.parse(raw).format(DateTimeFormatter.ofPattern("dd/MM • HH:mm"))
}.getOrDefault(raw.take(16))

private fun checkoutMoney(raw: String): String = NumberFormat.getCurrencyInstance(
    Locale.forLanguageTag("pt-BR"),
).format(raw.toBigDecimalOrNull() ?: BigDecimal.ZERO)
