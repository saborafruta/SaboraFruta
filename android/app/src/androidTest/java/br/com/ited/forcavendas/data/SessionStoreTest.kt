package br.com.ited.forcavendas.data

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class SessionStoreTest {
    private val context = ApplicationProvider.getApplicationContext<android.content.Context>()
    private lateinit var store: SessionStore

    @Before
    fun setUp() {
        context.getSharedPreferences("ited_mobile", android.content.Context.MODE_PRIVATE)
            .edit()
            .clear()
            .commit()
        store = SessionStore(context)
    }

    @After
    fun tearDown() {
        store.clearSession()
    }

    @Test
    fun savedSessionIsEncryptedAndCanBeRestored() {
        val session = UserSession(
            tokens = AuthTokens("access-secret", "refresh-secret"),
            usuarioNome = "Vendedor",
            usuarioEmail = "vendedor@ited.app.br",
            context = WorkContext(
                tenantAlias = "ited",
                empresaId = 1,
                empresaNome = "iTED",
                filialId = 2,
                filialNome = "Matriz",
                filialCnpj = "00000000000000",
                podeCriarPedido = true,
                podeSeparar = false,
            ),
        )

        store.setSecureAccessEnabled(true)
        store.saveSession(session)

        val preferences = context.getSharedPreferences(
            "ited_mobile",
            android.content.Context.MODE_PRIVATE,
        )
        assertFalse(preferences.contains("access"))
        assertFalse(preferences.contains("refresh"))
        assertNotNull(preferences.getString("encrypted_session_v1", null))
        assertEquals(session, store.loadSession())
    }
}
