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
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import br.com.ited.forcavendas.data.UserSession

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrdersScreen(
    repository: ItedRepository,
    session: UserSession,
    onBack: () -> Unit,
) {
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var orders by remember { mutableStateOf(emptyList<OrderSummary>()) }
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
            if (loading) item {
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                    CircularProgressIndicator()
                }
            }
            error?.let { item { Text(it, color = MaterialTheme.colorScheme.error) } }
            if (!loading && orders.isEmpty()) item { Text("Nenhum pedido encontrado.") }
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
