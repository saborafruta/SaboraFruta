package br.com.ited.forcavendas

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.fragment.app.FragmentActivity
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.SessionStore
import br.com.ited.forcavendas.security.DeviceAuthenticator
import br.com.ited.forcavendas.sync.ItedSyncWorker
import br.com.ited.forcavendas.ui.theme.ITEDTheme

class MainActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ItedSyncWorker.schedule(applicationContext)
        val deviceAuthenticator = DeviceAuthenticator(this)
        enableEdgeToEdge()
        setContent {
            val repository = remember { ItedRepository(SessionStore(applicationContext)) }
            val systemDarkTheme = isSystemInDarkTheme()
            var darkTheme by remember {
                mutableStateOf(repository.darkThemePreference() ?: systemDarkTheme)
            }
            ITEDTheme(darkTheme = darkTheme) {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                    contentColor = MaterialTheme.colorScheme.onBackground,
                ) {
                    AppRoot(
                        repository = repository,
                        darkTheme = darkTheme,
                        deviceSecurityAvailable = deviceAuthenticator.isAvailable,
                        onAuthenticate = deviceAuthenticator::authenticate,
                        onToggleTheme = {
                            darkTheme = !darkTheme
                            repository.saveDarkThemePreference(darkTheme)
                        },
                    )
                }
            }
        }
    }
}
