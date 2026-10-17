package br.com.ited.forcavendas.security

import android.app.KeyguardManager
import android.content.Context
import android.os.Build
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.fragment.app.FragmentActivity

class DeviceAuthenticator(private val activity: FragmentActivity) {
    private var onSuccess: (() -> Unit)? = null
    private var onError: ((String) -> Unit)? = null

    private val prompt = BiometricPrompt(
        activity,
        object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                super.onAuthenticationSucceeded(result)
                onSuccess?.invoke()
                clearCallbacks()
            }

            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                super.onAuthenticationError(errorCode, errString)
                if (errorCode !in listOf(
                        BiometricPrompt.ERROR_CANCELED,
                        BiometricPrompt.ERROR_USER_CANCELED,
                        BiometricPrompt.ERROR_NEGATIVE_BUTTON,
                    )
                ) {
                    onError?.invoke(errString.toString())
                }
                clearCallbacks()
            }
        },
    )

    val isAvailable: Boolean
        get() = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            BiometricManager.from(activity).canAuthenticate(AUTHENTICATORS) ==
                BiometricManager.BIOMETRIC_SUCCESS
        } else {
            val hasBiometric = BiometricManager.from(activity).canAuthenticate(
                BiometricManager.Authenticators.BIOMETRIC_WEAK,
            ) == BiometricManager.BIOMETRIC_SUCCESS
            val keyguard = activity.getSystemService(Context.KEYGUARD_SERVICE) as KeyguardManager
            hasBiometric || keyguard.isDeviceSecure
        }

    fun authenticate(onSuccess: () -> Unit, onError: (String) -> Unit) {
        if (!isAvailable) {
            onError("Configure biometria, PIN, padrão ou senha no aparelho para usar o acesso protegido.")
            return
        }
        this.onSuccess = onSuccess
        this.onError = onError
        val builder = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Entrar no iTED")
            .setSubtitle("Confirme sua identidade para acessar a Força de Vendas")

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            builder.setAllowedAuthenticators(AUTHENTICATORS)
        } else {
            @Suppress("DEPRECATION")
            builder.setDeviceCredentialAllowed(true)
        }
        prompt.authenticate(builder.build())
    }

    private fun clearCallbacks() {
        onSuccess = null
        onError = null
    }

    private companion object {
        const val AUTHENTICATORS =
            BiometricManager.Authenticators.BIOMETRIC_STRONG or
                BiometricManager.Authenticators.DEVICE_CREDENTIAL
    }
}
