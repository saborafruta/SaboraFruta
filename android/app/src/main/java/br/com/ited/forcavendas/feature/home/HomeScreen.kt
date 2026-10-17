package br.com.ited.forcavendas.feature.home

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ReceiptLong
import androidx.compose.material.icons.outlined.CloudDone
import androidx.compose.material.icons.outlined.Inventory2
import androidx.compose.material.icons.outlined.People
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import br.com.ited.forcavendas.R

private data class HomeAction(
    val title: String,
    val description: String,
    val icon: ImageVector,
)

private val actions = listOf(
    HomeAction("Novo pedido", "Cliente, produtos, preço e pagamento", Icons.AutoMirrored.Outlined.ReceiptLong),
    HomeAction("Meus pedidos", "Envio e acompanhamento pelo número oficial", Icons.Outlined.CloudDone),
    HomeAction("Clientes", "Carteira, crédito e tabela comercial", Icons.Outlined.People),
    HomeAction("Separação", "Depósito, lote e quantidade conferida", Icons.Outlined.Inventory2),
)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen() {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("ITED Força de Vendas", fontWeight = FontWeight.Bold)
                        Text(
                            "Empresa e filial serão exibidas aqui",
                            style = MaterialTheme.typography.labelSmall,
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
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
                Box(
                    modifier = Modifier.fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    Image(
                        painter = painterResource(R.drawable.logo_ited),
                        contentDescription = "ITED",
                        modifier = Modifier.size(124.dp),
                        contentScale = ContentScale.Fit,
                    )
                }
            }
            item { SyncCard() }
            item {
                Spacer(Modifier.height(4.dp))
                Text(
                    "Operação",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
            }
            items(actions) { action ->
                ActionCard(action)
            }
        }
    }
}

@Composable
private fun SyncCard() {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.primaryContainer,
        ),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column {
                    Text("Sincronização", fontWeight = FontWeight.Bold)
                    Text(
                        "Aguardando primeiro acesso",
                        style = MaterialTheme.typography.bodySmall,
                    )
                }
                Icon(Icons.Outlined.CloudDone, contentDescription = null)
            }
            Button(
                modifier = Modifier.fillMaxWidth(),
                onClick = { },
            ) {
                Text("Sincronizar agora")
            }
        }
    }
}

@Composable
private fun ActionCard(action: HomeAction) {
    Surface(
        modifier = Modifier.fillMaxWidth(),
        shape = MaterialTheme.shapes.large,
        tonalElevation = 1.dp,
    ) {
        Row(
            modifier = Modifier.padding(16.dp),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = action.icon,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
            )
            Column {
                Text(action.title, fontWeight = FontWeight.Bold)
                Text(action.description, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
