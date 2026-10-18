package br.com.ited.forcavendas.feature.order

import java.math.BigDecimal
import org.junit.Assert.assertEquals
import org.junit.Test

class NewOrderQuantityTest {
    @Test
    fun increasesQuantityWithoutChangingTheProductLine() {
        assertEquals("2", adjustedOrderQuantity("1", BigDecimal.ONE))
        assertEquals("2.5", adjustedOrderQuantity("1.5", BigDecimal.ONE))
        assertEquals("1", adjustedOrderQuantity("", BigDecimal.ONE))
    }

    @Test
    fun decreasesQuantityWithoutReachingZero() {
        assertEquals("1", adjustedOrderQuantity("2", BigDecimal.ONE.negate()))
        assertEquals("0.001", adjustedOrderQuantity("1", BigDecimal.ONE.negate()))
        assertEquals("0.001", adjustedOrderQuantity("0.001", BigDecimal.ONE.negate()))
    }
}
