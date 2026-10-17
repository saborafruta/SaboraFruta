package br.com.ited.forcavendas.ui.theme

import androidx.compose.material3.Typography
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import br.com.ited.forcavendas.R

val InterFontFamily = FontFamily(
    Font(R.font.inter_variable, FontWeight.Normal),
    Font(R.font.inter_variable, FontWeight.Medium),
    Font(R.font.inter_variable, FontWeight.SemiBold),
    Font(R.font.inter_variable, FontWeight.Bold),
)

val SpaceGroteskFontFamily = FontFamily(
    Font(R.font.space_grotesk_variable, FontWeight.Bold),
)

private val DefaultTypography = Typography()

internal val ItedTypography = Typography(
    displayLarge = DefaultTypography.displayLarge.copy(fontFamily = InterFontFamily),
    displayMedium = DefaultTypography.displayMedium.copy(fontFamily = InterFontFamily),
    displaySmall = DefaultTypography.displaySmall.copy(fontFamily = InterFontFamily),
    headlineLarge = DefaultTypography.headlineLarge.copy(fontFamily = InterFontFamily),
    headlineMedium = DefaultTypography.headlineMedium.copy(fontFamily = InterFontFamily),
    headlineSmall = DefaultTypography.headlineSmall.copy(fontFamily = InterFontFamily),
    titleLarge = DefaultTypography.titleLarge.copy(fontFamily = InterFontFamily),
    titleMedium = DefaultTypography.titleMedium.copy(fontFamily = InterFontFamily),
    titleSmall = DefaultTypography.titleSmall.copy(fontFamily = InterFontFamily),
    bodyLarge = DefaultTypography.bodyLarge.copy(fontFamily = InterFontFamily),
    bodyMedium = DefaultTypography.bodyMedium.copy(fontFamily = InterFontFamily),
    bodySmall = DefaultTypography.bodySmall.copy(fontFamily = InterFontFamily),
    labelLarge = DefaultTypography.labelLarge.copy(fontFamily = InterFontFamily),
    labelMedium = DefaultTypography.labelMedium.copy(fontFamily = InterFontFamily),
    labelSmall = DefaultTypography.labelSmall.copy(fontFamily = InterFontFamily),
)
