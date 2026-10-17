package br.com.ited.forcavendas

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.LoginResult
import br.com.ited.forcavendas.data.NetworkException
import br.com.ited.forcavendas.data.UserSession
import br.com.ited.forcavendas.feature.auth.ContextSelectionScreen
import br.com.ited.forcavendas.feature.auth.LoginScreen
import br.com.ited.forcavendas.feature.home.HomeScreen
import br.com.ited.forcavendas.feature.order.NewOrderScreen
import br.com.ited.forcavendas.feature.order.OrdersScreen
import br.com.ited.forcavendas.feature.separation.SeparationScreen
import br.com.ited.forcavendas.sync.SyncStatus
import java.time.Instant
import kotlinx.coroutines.launch

private enum class AppScreen { LOGIN, CONTEXT, HOME, NEW_ORDER, ORDERS, SEPARATION }

@Composable
fun AppRoot(repository: ItedRepository) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var session by remember { mutableStateOf<UserSession?>(repository.savedSession()) }
    var loginResult by remember { mutableStateOf<LoginResult?>(null) }
    var screen by remember {
        mutableStateOf(if (session == null) AppScreen.LOGIN else AppScreen.HOME)
    }
    var loginLoading by remember { mutableStateOf(false) }
    var loginError by remember { mutableStateOf<String?>(null) }
    var pending by remember { mutableIntStateOf(repository.pendingCount()) }
    var syncStatus by remember {
        mutableStateOf<SyncStatus>(
            if (pending > 0) SyncStatus.Pending(pending) else SyncStatus.Idle,
        )
    }

    fun notify(text: String) {
        scope.launch { snackbar.showSnackbar(text) }
    }

    fun goHome(message: String? = null) {
        pending = repository.pendingCount()
        syncStatus = if (pending > 0) SyncStatus.Pending(pending) else SyncStatus.Idle
        screen = AppScreen.HOME
        message?.let(::notify)
    }

    BackHandler(enabled = screen !in listOf(AppScreen.LOGIN, AppScreen.HOME)) { goHome() }

    Box(modifier = Modifier.fillMaxSize()) {
        when (screen) {
            AppScreen.LOGIN -> LoginScreen(
                loading = loginLoading,
                error = loginError,
                onLogin = { email, password ->
                    scope.launch {
                        loginLoading = true
                        loginError = null
                        runCatching { repository.login(email, password) }
                            .onSuccess { result ->
                                loginResult = result
                                when (result.contexts.size) {
                                    0 -> loginError = "Seu usuário não possui empresa e filial disponíveis."
                                    1 -> {
                                        session = repository.selectContext(result, result.contexts.first())
                                        screen = AppScreen.HOME
                                    }
                                    else -> screen = AppScreen.CONTEXT
                                }
                            }
                            .onFailure {
                                loginError = it.message ?: "Não foi possível entrar no ITED."
                            }
                        loginLoading = false
                    }
                },
            )

            AppScreen.CONTEXT -> loginResult?.let { result ->
                ContextSelectionScreen(result) { context ->
                    session = repository.selectContext(result, context)
                    screen = AppScreen.HOME
                }
            }

            AppScreen.HOME -> session?.let { current ->
                HomeScreen(
                    session = current,
                    syncStatus = syncStatus,
                    pendingOperations = pending,
                    onSync = {
                        scope.launch {
                            syncStatus = SyncStatus.Syncing
                            runCatching { repository.sync(current) }
                                .onSuccess { summary ->
                                    pending = summary.pendingAfter
                                    syncStatus = if (pending > 0) {
                                        SyncStatus.Pending(pending)
                                    } else {
                                        SyncStatus.Synced(Instant.now())
                                    }
                                    val text = if (summary.sentOrders > 0) {
                                        "${summary.sentOrders} pedido(s) enviado(s) ao ITED."
                                    } else {
                                        "Dados sincronizados."
                                    }
                                    notify(text)
                                }
                                .onFailure { error ->
                                    syncStatus = if (error is NetworkException) {
                                        SyncStatus.Offline
                                    } else {
                                        SyncStatus.Failed(error.message ?: "Falha de sincronização")
                                    }
                                }
                        }
                    },
                    onNewOrder = { screen = AppScreen.NEW_ORDER },
                    onOrders = { screen = AppScreen.ORDERS },
                    onSeparation = { screen = AppScreen.SEPARATION },
                    onLogout = {
                        repository.logout()
                        session = null
                        loginResult = null
                        screen = AppScreen.LOGIN
                    },
                )
            }

            AppScreen.NEW_ORDER -> session?.let { current ->
                NewOrderScreen(
                    repository = repository,
                    session = current,
                    onBack = { goHome() },
                    onSubmitted = { goHome(it) },
                )
            }

            AppScreen.ORDERS -> session?.let { current ->
                OrdersScreen(repository, current) { goHome() }
            }

            AppScreen.SEPARATION -> session?.let { current ->
                SeparationScreen(
                    repository = repository,
                    session = current,
                    onBack = { goHome() },
                    onCompleted = { goHome(it) },
                )
            }
        }
        SnackbarHost(
            hostState = snackbar,
            modifier = Modifier.align(Alignment.BottomCenter),
        )
    }
}
