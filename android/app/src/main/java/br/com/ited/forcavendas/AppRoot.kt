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
import br.com.ited.forcavendas.feature.auth.UnlockScreen
import br.com.ited.forcavendas.feature.client.ClientsScreen
import br.com.ited.forcavendas.feature.home.HomeScreen
import br.com.ited.forcavendas.feature.order.NewOrderScreen
import br.com.ited.forcavendas.feature.order.OrdersScreen
import br.com.ited.forcavendas.feature.separation.SeparationScreen
import br.com.ited.forcavendas.feature.sync.SyncCenterScreen
import br.com.ited.forcavendas.sync.SyncStatus
import java.time.Instant
import kotlinx.coroutines.launch

private enum class AppScreen { LOCKED, LOGIN, CONTEXT, HOME, NEW_ORDER, ORDERS, CLIENTS, SEPARATION, SYNC }

@Composable
fun AppRoot(
    repository: ItedRepository,
    darkTheme: Boolean,
    deviceSecurityAvailable: Boolean,
    onAuthenticate: (onSuccess: () -> Unit, onError: (String) -> Unit) -> Unit,
    onToggleTheme: () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var session by remember { mutableStateOf<UserSession?>(repository.savedSession()) }
    var loginResult by remember { mutableStateOf<LoginResult?>(null) }
    var screen by remember {
        mutableStateOf(if (session == null) AppScreen.LOGIN else AppScreen.LOCKED)
    }
    var loginLoading by remember { mutableStateOf(false) }
    var loginError by remember { mutableStateOf<String?>(null) }
    var unlockError by remember { mutableStateOf<String?>(null) }
    var contextLoading by remember { mutableStateOf(false) }
    var rememberSecureAccess by remember { mutableStateOf(false) }
    var pending by remember {
        mutableIntStateOf(session?.let { repository.pendingCount(it) } ?: 0)
    }
    var syncStatus by remember {
        mutableStateOf<SyncStatus>(
            if (pending > 0) SyncStatus.Pending(pending) else SyncStatus.Idle,
        )
    }

    fun notify(text: String) {
        scope.launch { snackbar.showSnackbar(text) }
    }

    fun goHome(message: String? = null) {
        pending = session?.let { repository.pendingCount(it) } ?: 0
        syncStatus = if (pending > 0) {
            SyncStatus.Pending(pending)
        } else {
            session?.let { current ->
                repository.syncDashboard(current).lastSuccessAt?.let { raw ->
                    runCatching { SyncStatus.Synced(Instant.parse(raw)) }.getOrNull()
                }
            } ?: SyncStatus.Idle
        }
        screen = AppScreen.HOME
        message?.let(::notify)
    }

    fun synchronize(current: UserSession) {
        scope.launch {
            syncStatus = SyncStatus.Syncing
            runCatching { repository.sync(current) }
                .onSuccess { summary ->
                    pending = summary.pendingAfter
                    syncStatus = when {
                        pending > 0 -> SyncStatus.Pending(pending)
                        summary.errors.isNotEmpty() -> SyncStatus.Failed(summary.errors.first())
                        else -> SyncStatus.Synced(Instant.now())
                    }
                    val text = when {
                        summary.sentOrders > 0 ->
                            "${summary.sentOrders} pedido(s) enviado(s) ao ITED."
                        summary.errors.isNotEmpty() ->
                            "Sincronização parcial. Consulte os detalhes."
                        else -> "Dados sincronizados."
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
    }

    fun requestUnlock() {
        unlockError = null
        onAuthenticate(
            {
                unlockError = null
                screen = AppScreen.HOME
            },
            { message -> unlockError = message },
        )
    }

    LaunchedEffect(Unit) {
        if (screen == AppScreen.LOCKED) requestUnlock()
    }

    BackHandler(enabled = screen !in listOf(AppScreen.LOCKED, AppScreen.LOGIN, AppScreen.HOME)) {
        if (screen == AppScreen.CONTEXT && session == null) {
            screen = AppScreen.LOGIN
        } else {
            goHome()
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        when (screen) {
            AppScreen.LOCKED -> session?.let { saved ->
                UnlockScreen(
                    userName = saved.usuarioNome,
                    error = unlockError,
                    onUnlock = ::requestUnlock,
                    onUseAnotherAccount = {
                        repository.logout()
                        session = null
                        loginResult = null
                        screen = AppScreen.LOGIN
                    },
                )
            }

            AppScreen.LOGIN -> LoginScreen(
                loading = loginLoading,
                error = loginError,
                deviceSecurityAvailable = deviceSecurityAvailable,
                onLogin = { email, password, secureAccess ->
                    scope.launch {
                        loginLoading = true
                        loginError = null
                        rememberSecureAccess = secureAccess
                        runCatching { repository.login(email, password) }
                            .onSuccess { result ->
                                loginResult = result
                                when (result.contexts.size) {
                                    0 -> loginError = "Seu usuário não possui empresa e filial disponíveis."
                                    1 -> {
                                        val selected = repository.selectContext(
                                            result,
                                            result.contexts.first(),
                                            rememberSecureAccess,
                                        )
                                        session = selected
                                        pending = repository.pendingCount(selected)
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
                ContextSelectionScreen(
                    result = result,
                    currentContext = session?.context,
                    onBack = session?.let { { goHome() } },
                    onSelect = { context ->
                        val selected = repository.selectContext(
                            result,
                            context,
                            secureAccess = if (session == null) rememberSecureAccess else null,
                        )
                        session = selected
                        pending = repository.pendingCount(selected)
                        syncStatus = if (pending > 0) {
                            SyncStatus.Pending(pending)
                        } else {
                            SyncStatus.Idle
                        }
                        screen = AppScreen.HOME
                    },
                )
            }

            AppScreen.HOME -> session?.let { current ->
                HomeScreen(
                    session = current,
                    syncStatus = syncStatus,
                    pendingOperations = pending,
                    onSync = { synchronize(current) },
                    onOpenSync = { screen = AppScreen.SYNC },
                    onNewOrder = { screen = AppScreen.NEW_ORDER },
                    onOrders = { screen = AppScreen.ORDERS },
                    onClients = { screen = AppScreen.CLIENTS },
                    onSeparation = { screen = AppScreen.SEPARATION },
                    contextLoading = contextLoading,
                    onSwitchContext = {
                        scope.launch {
                            contextLoading = true
                            runCatching { repository.contexts(current) }
                                .onSuccess { result ->
                                    loginResult = result
                                    screen = AppScreen.CONTEXT
                                }
                                .onFailure { error ->
                                    notify(error.message ?: "Não foi possível carregar as filiais.")
                                }
                            contextLoading = false
                        }
                    },
                    darkTheme = darkTheme,
                    onToggleTheme = onToggleTheme,
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
                    onDraftSaved = ::notify,
                )
            }

            AppScreen.ORDERS -> session?.let { current ->
                OrdersScreen(
                    repository = repository,
                    session = current,
                    onContinueDraft = { screen = AppScreen.NEW_ORDER },
                    onBack = { goHome() },
                )
            }

            AppScreen.CLIENTS -> session?.let { current ->
                ClientsScreen(repository, current) { goHome() }
            }

            AppScreen.SEPARATION -> session?.let { current ->
                SeparationScreen(
                    repository = repository,
                    session = current,
                    onBack = { goHome() },
                    onCompleted = { goHome(it) },
                )
            }

            AppScreen.SYNC -> session?.let { current ->
                SyncCenterScreen(
                    session = current,
                    dashboard = repository.syncDashboard(current),
                    status = syncStatus,
                    onSync = { synchronize(current) },
                    onBack = { goHome() },
                )
            }
        }
        SnackbarHost(
            hostState = snackbar,
            modifier = Modifier.align(Alignment.BottomCenter),
        )
    }
}
