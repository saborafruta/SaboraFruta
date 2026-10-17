package br.com.ited.forcavendas

import android.os.Bundle
import androidx.activity.ComponentActivity
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
import br.com.ited.forcavendas.data.ItedRepository
import br.com.ited.forcavendas.data.SessionStore
import br.com.ited.forcavendas.sync.ItedSyncWorker
import br.com.ited.forcavendas.ui.theme.ITEDTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ItedSyncWorker.schedule(applicationContext)
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
