#include "board.h"
#include "secure.h"
#include "zirah_config.h"
#include "board_pins.h"

#include <errno.h>
#include <stddef.h>

#include <libopencm3/stm32/rcc.h>
#include <libopencm3/stm32/gpio.h>
#include <libopencm3/stm32/flash.h>
#include <libopencm3/stm32/usart.h>
#include <libopencm3/stm32/timer.h>
#include <libopencm3/cm3/systick.h>
#include <libopencm3/cm3/dwt.h>
#include <libopencm3/cm3/nvic.h>
#include <libopencm3/cm3/scb.h>

/* Linker symbols (libopencm3 cortex-m-generic.ld). */
extern uint32_t _ebss;
extern uint32_t _stack;

static volatile uint32_t g_ms;

void sys_tick_handler(void)
{
    g_ms++;
}

uint32_t millis(void)
{
    return g_ms;
}

uint32_t cycles_now(void)
{
    return dwt_read_cycle_counter();
}

uint32_t cycles_to_us(uint32_t cycles)
{
    return cycles / (ZIRAH_CPU_HZ / 1000000UL);
}

void delay_us(uint32_t us)
{
    uint32_t start = dwt_read_cycle_counter();
    uint32_t wait = us * (ZIRAH_CPU_HZ / 1000000UL);
    while ((uint32_t)(dwt_read_cycle_counter() - start) < wait) {
    }
}

void delay_ms(uint32_t ms)
{
    uint32_t start = g_ms;
    while ((uint32_t)(g_ms - start) < ms) {
    }
}

#if defined(ZIRAH_BOARD_DEVEBOX)
/* One user LED (D2) on PA1, lit when the pin is low. It shows LED_RED, the
 * fault and attention colour; the OLED shows everything else. */
void led_set(uint32_t mask, bool on)
{
    if (mask & LED_RED) {
        if (on) {
            gpio_clear(GPIOA, GPIO1);
        } else {
            gpio_set(GPIOA, GPIO1);
        }
    }
}

void led_only(uint32_t mask)
{
    led_set(LED_RED, (mask & LED_RED) != 0);
}
#else
void led_set(uint32_t mask, bool on)
{
    if (on) {
        gpio_set(GPIOD, mask);
    } else {
        gpio_clear(GPIOD, mask);
    }
}

void led_only(uint32_t mask)
{
    gpio_clear(GPIOD, LED_GREEN | LED_ORANGE | LED_RED | LED_BLUE);
    gpio_set(GPIOD, mask);
}
#endif

bool button_pressed(void)
{
    return gpio_get(GPIOA, GPIO0) != 0;
}

void board_uid(uint8_t out[12])
{
    /* RM0090 39.1: 96-bit unique ID at 0x1FFF7A10. */
    const volatile uint8_t *uid = (const volatile uint8_t *)0x1FFF7A10UL;
    for (int i = 0; i < 12; i++) {
        out[i] = uid[i];
    }
}

#if defined(ZIRAH_BOARD_DEVEBOX)
/* The crystal fitted to a generic board is not certain, and with a wrong
 * guess the serial link runs at the wrong baud rate and nothing answers. So
 * before the PLL is set up, while the CPU still runs from the 16 MHz HSI,
 * the HSE is started with a timeout and measured: TIM11 channel 1 can take
 * HSE / RTCPRE inside the chip (TIM11_OR, RM0090 18.4.13), and with
 * RTCPRE = 8 and the input prescaler at 8 each capture spans 64 HSE cycles,
 * 128 HSI ticks for an 8 MHz crystal. The nearest of 8, 12, 16 and 25 MHz
 * within 5 % is used; anything else, or no crystal, falls back to the HSI. */
#define TIM11_OR_R (*(volatile uint32_t *)(TIM11 + 0x50u))
static uint32_t g_hse_mhz;   /* 0: running from the HSI */

static uint32_t hse_measure_khz(void)
{
    RCC_CR |= RCC_CR_HSEON;
    for (uint32_t spin = 0; !(RCC_CR & RCC_CR_HSERDY); spin++) {
        if (spin > 2000000u) {
            RCC_CR &= ~RCC_CR_HSEON;
            return 0;
        }
    }
    RCC_CFGR = (RCC_CFGR & ~(RCC_CFGR_RTCPRE_MASK << RCC_CFGR_RTCPRE_SHIFT)) | (8u << RCC_CFGR_RTCPRE_SHIFT);
    rcc_periph_clock_enable(RCC_TIM11);
    TIM_CR1(TIM11) = 0;
    TIM_PSC(TIM11) = 0;
    TIM_ARR(TIM11) = 0xFFFFu;
    TIM11_OR_R = 2u;                                 /* TI1 = HSE_RTC */
    TIM_CCMR1(TIM11) = TIM_CCMR1_CC1S_IN_TI1 | TIM_CCMR1_IC1PSC_8;
    TIM_CCER(TIM11) = TIM_CCER_CC1E;
    TIM_EGR(TIM11) = TIM_EGR_UG;
    TIM_SR(TIM11) = 0;
    TIM_CR1(TIM11) = TIM_CR1_CEN;
    uint32_t prev = 0, sum = 0;
    for (int i = -1; i < 32; i++) {
        uint32_t spin = 0;
        while (!(TIM_SR(TIM11) & TIM_SR_CC1IF)) {
            if (++spin > 200000u) {
                sum = 0;
                goto done;
            }
        }
        uint32_t now = TIM_CCR1(TIM11);
        if (i >= 0) {
            sum += (now - prev) & 0xFFFFu;
        }
        prev = now;
    }
done:
    TIM_CR1(TIM11) = 0;
    TIM11_OR_R = 0;
    rcc_periph_clock_disable(RCC_TIM11);
    RCC_CFGR &= ~(RCC_CFGR_RTCPRE_MASK << RCC_CFGR_RTCPRE_SHIFT);
    /* 32 captures of 64 HSE cycles each, counted at 16 MHz. */
    return sum ? (uint32_t)((16000ull * 64u * 32u) / sum) : 0;
}

static void clock_setup(void)
{
    static const struct {
        uint32_t mhz;
        const struct rcc_clock_scale *cfg;
    } k[] = {
        {8, &rcc_hse_8mhz_3v3[RCC_CLOCK_3V3_168MHZ]},
        {12, &rcc_hse_12mhz_3v3[RCC_CLOCK_3V3_168MHZ]},
        {16, &rcc_hse_16mhz_3v3[RCC_CLOCK_3V3_168MHZ]},
        {25, &rcc_hse_25mhz_3v3[RCC_CLOCK_3V3_168MHZ]},
    };
    uint32_t khz = hse_measure_khz();
    for (unsigned i = 0; i < sizeof(k) / sizeof(k[0]); i++) {
        uint32_t want = k[i].mhz * 1000u;
        if (khz * 20u >= want * 19u && khz * 20u <= want * 21u) {
            g_hse_mhz = k[i].mhz;
            rcc_clock_setup_pll(k[i].cfg);
            return;
        }
    }
    RCC_CR &= ~RCC_CR_HSEON;
    g_hse_mhz = 0;
    rcc_clock_setup_pll(&rcc_hsi_configs[RCC_CLOCK_3V3_168MHZ]);
}

uint32_t board_hse_mhz(void)
{
    return g_hse_mhz;
}
#else
static void clock_setup(void)
{
    /* 8 MHz HSE -> 168 MHz SYSCLK, 42 MHz APB1, 84 MHz APB2, 48 MHz USB. */
    rcc_clock_setup_pll(&rcc_hse_8mhz_3v3[RCC_CLOCK_3V3_168MHZ]);
}

uint32_t board_hse_mhz(void)
{
    return 8;
}
#endif

void board_init(void)
{
    clock_setup();

    rcc_periph_clock_enable(RCC_GPIOA);
    rcc_periph_clock_enable(RCC_GPIOB);
    rcc_periph_clock_enable(RCC_GPIOC);
    rcc_periph_clock_enable(RCC_GPIOD);

    /* 1 ms SysTick. */
    systick_set_clocksource(STK_CSR_CLKSOURCE_AHB);
    systick_set_reload(ZIRAH_CPU_HZ / 1000 - 1);
    systick_clear();
    systick_interrupt_enable();
    systick_counter_enable();

    dwt_enable_cycle_counter();

    /* Trap integer division by zero instead of silently returning 0. */
    SCB_CCR |= SCB_CCR_DIV_0_TRP;
    /* Route MemManage, BusFault and UsageFault to their own handlers. */
    SCB_SHCSR |= SCB_SHCSR_MEMFAULTENA | SCB_SHCSR_BUSFAULTENA | SCB_SHCSR_USGFAULTENA;

#if defined(ZIRAH_BOARD_DEVEBOX)
    /* User LED D2 on PA1 (lit when low), off. */
    gpio_set(GPIOA, GPIO1);
    gpio_mode_setup(GPIOA, GPIO_MODE_OUTPUT, GPIO_PUPD_NONE, GPIO1);
    led_only(0);

    /* K1 on PA0, active high; the internal pull-down keeps it low whether
     * or not the board fits its own. */
    gpio_mode_setup(GPIOA, GPIO_MODE_INPUT, GPIO_PUPD_PULLDOWN, GPIO0);
#else
    /* LEDs. */
    gpio_mode_setup(GPIOD, GPIO_MODE_OUTPUT, GPIO_PUPD_NONE,
                    LED_GREEN | LED_ORANGE | LED_RED | LED_BLUE);
    led_only(0);

    /* USER button (the board has an external pull-down). */
    gpio_mode_setup(GPIOA, GPIO_MODE_INPUT, GPIO_PUPD_NONE, GPIO0);

    /* Keep the OTG VBUS power switch disabled (active low enable). */
    gpio_set(GPIOC, GPIO0);
    gpio_mode_setup(GPIOC, GPIO_MODE_OUTPUT, GPIO_PUPD_NONE, GPIO0);
#endif
}

/* ------------------------------------------------------------------------ */
/* MPU (ARMv7-M PMSA, 8 regions on the Cortex-M4)                            */
/* ------------------------------------------------------------------------ */

#define MPU_CTRL_R (*(volatile uint32_t *)0xE000ED94UL)
#define MPU_RNR_R  (*(volatile uint32_t *)0xE000ED98UL)
#define MPU_RBAR_R (*(volatile uint32_t *)0xE000ED9CUL)
#define MPU_RASR_R (*(volatile uint32_t *)0xE000EDA0UL)

#define RASR_ENABLE     (1u << 0)
#define RASR_SIZE(log2) ((uint32_t)((log2) - 1u) << 1)  /* region = 2^log2 bytes */
#define RASR_NORMAL_WB  ((1u << 19) | (1u << 17) | (1u << 16)) /* TEX=1 C=1 B=1 */
#define RASR_AP_RW      (3u << 24)
#define RASR_AP_NONE    (0u << 24)
#define RASR_XN         (1u << 28)

#define GUARD_LOG2  8u          /* 256-byte stack guard */
#define GUARD_BYTES (1u << GUARD_LOG2)

static bool g_mpu_on;
static uint32_t g_stack_bytes;

static void mpu_region(uint32_t n, uint32_t base, uint32_t rasr)
{
    MPU_RNR_R = n;
    MPU_RBAR_R = base;
    MPU_RASR_R = rasr;
}

bool board_mpu_init(uint32_t min_stack)
{
    uint32_t ebss = (uint32_t)&_ebss;
    uint32_t top = (uint32_t)&_stack;
    uint32_t guard = (ebss + GUARD_BYTES - 1u) & ~(GUARD_BYTES - 1u);
    if (guard + GUARD_BYTES >= top) {
        return false;
    }
    g_stack_bytes = top - (guard + GUARD_BYTES);
    if (g_stack_bytes < min_stack) {
        return false;
    }
#if ZIRAH_ENABLE_MPU
    __asm__ volatile("dsb");
    MPU_CTRL_R = 0;
    /* 0: main SRAM 128 KB, read-write, never executable. */
    mpu_region(0, 0x20000000UL, RASR_ENABLE | RASR_SIZE(17) | RASR_NORMAL_WB |
                                    RASR_AP_RW | RASR_XN);
    /* 1: CCM 64 KB (masked key), read-write, never executable. */
    mpu_region(1, 0x10000000UL, RASR_ENABLE | RASR_SIZE(16) | RASR_NORMAL_WB |
                                    RASR_AP_RW | RASR_XN);
    /* 2: stack guard just above .bss. Higher region number wins. */
    mpu_region(2, guard, RASR_ENABLE | RASR_SIZE(GUARD_LOG2) | RASR_NORMAL_WB |
                             RASR_AP_NONE | RASR_XN);
    for (uint32_t n = 3; n < 8; n++) {
        mpu_region(n, 0, 0);
    }
    /* PRIVDEFENA keeps the default map for flash and peripherals. */
    MPU_CTRL_R = (1u << 2) | (1u << 0);
    __asm__ volatile("dsb\n isb");
    g_mpu_on = true;
#endif
    return true;
}

bool board_mpu_enabled(void)
{
    return g_mpu_on;
}

uint32_t board_stack_bytes(void)
{
    return g_stack_bytes;
}

unsigned board_rdp_level(void)
{
    /* RM0090 3.6.3: RDP is FLASH_OPTCR[15:8]. 0xAA is level 0, 0xCC is level
     * 2 and every other value is level 1. */
    uint32_t rdp = (FLASH_OPTCR >> 8) & 0xFFu;
    return rdp == 0xAAu ? 0u : rdp == 0xCCu ? 2u : 1u;
}

/* ------------------------------------------------------------------------ */
/* Stack protector                                                           */
/* ------------------------------------------------------------------------ */

/* Initial value is replaced from the TRNG during boot. The low byte is kept
 * zero so a string overflow cannot reproduce the canary. */
uint32_t __stack_chk_guard = 0x5A1E3C00u;

void board_set_stack_guard(uint32_t value)
{
    __stack_chk_guard = value & 0xFFFFFF00u;
}

/* ------------------------------------------------------------------------ */
/* Heap: deliberately disabled                                               */
/* ------------------------------------------------------------------------ */

/*
 * newlib links malloc through snprintf. With libopencm3's linker script the
 * heap would start at the beginning of CCM, which is exactly where the
 * masked key lives, so every allocation must fail instead.
 */
void *_sbrk(ptrdiff_t incr);
void *_sbrk(ptrdiff_t incr)
{
    (void)incr;
    errno = ENOMEM;
    return (void *)-1;
}

/* ------------------------------------------------------------------------ */
/* Faults                                                                    */
/* ------------------------------------------------------------------------ */

#define FAULT_STACK_BYTES 1024
uint64_t g_fault_stack[FAULT_STACK_BYTES / 8];

static void (*g_fault_wiper)(void);
static volatile bool g_in_fault;

void board_set_fault_wiper(void (*fn)(void))
{
    g_fault_wiper = fn;
}

/* Weak hook so the display code can show the fault; main.c provides it. */
__attribute__((weak)) void zirah_fault_display(uint32_t code, uint32_t cfsr)
{
    (void)code;
    (void)cfsr;
}

static void fault_uart(const char *s)
{
#if ZIRAH_ENABLE_UART
    /* Only if the USART was brought up; a disabled peripheral would never
     * report TXE and the halt loop below would never be reached. */
    if ((USART_CR1(UART_DEV) & USART_CR1_UE) == 0) {
        return;
    }
    while (*s) {
        uint32_t t0 = dwt_read_cycle_counter();
        while ((USART_SR(UART_DEV) & USART_SR_TXE) == 0) {
            if ((uint32_t)(dwt_read_cycle_counter() - t0) > ZIRAH_CPU_HZ / 100u) {
                return;
            }
        }
        USART_DR(UART_DEV) = (uint16_t)*s++;
    }
#else
    (void)s;
#endif
}

__attribute__((noreturn)) void zirah_fault_entry(uint32_t code)
{
    __asm__ volatile("cpsid i");
    uint32_t cfsr = SCB_CFSR;
    bool first = !g_in_fault;
    g_in_fault = true;

    /* Secrets first: the masked key and every work buffer live in CCM. */
    zirah_memzero((void *)0x10000000UL, 64u * 1024u);
    if (first && g_fault_wiper) {
        g_fault_wiper();
    }
    led_only(LED_RED);
    if (first) {
        static const char hx[] = "0123456789ABCDEF";
        char msg[40] = "\r\nFATAL FAULT x CFSR 00000000\r\n";
        msg[14] = hx[code & 15u];
        for (int i = 0; i < 8; i++) {
            msg[21 + i] = hx[(cfsr >> (28 - 4 * i)) & 15u];
        }
        fault_uart(msg);
        zirah_fault_display(code, cfsr);
    }
    /* Halt. No automatic reset: a glitch attacker should not get free retries. */
    for (;;) {
        led_set(LED_RED, true);
        delay_us(120000);
        led_set(LED_RED, false);
        delay_us(120000);
    }
}

#define STR2(x) #x
#define STR(x) STR2(x)

/* Each handler switches to the private fault stack before touching memory,
 * so a fault caused by stack overflow can still be handled cleanly. */
#define FAULT_TRAMPOLINE(name, code)                                         \
    __attribute__((naked, noreturn)) void name(void)                        \
    {                                                                         \
        __asm__ volatile("ldr r0, =g_fault_stack + " STR(FAULT_STACK_BYTES) "\n" \
                         "msr msp, r0\n"                                     \
                         "movs r0, #" STR(code) "\n"                         \
                         "b zirah_fault_entry\n"                             \
                         ".ltorg\n");                                         \
    }

/* Literal codes because the assembler needs plain numbers; they must match
 * ZIRAH_FAULT_HARD .. ZIRAH_FAULT_CANARY in board.h. */
FAULT_TRAMPOLINE(hard_fault_handler, 1)
FAULT_TRAMPOLINE(mem_manage_handler, 2)
FAULT_TRAMPOLINE(bus_fault_handler, 3)
FAULT_TRAMPOLINE(usage_fault_handler, 4)
FAULT_TRAMPOLINE(__stack_chk_fail, 5)
_Static_assert(ZIRAH_FAULT_HARD == 1 && ZIRAH_FAULT_MEM == 2 && ZIRAH_FAULT_BUS == 3 &&
                   ZIRAH_FAULT_USAGE == 4 && ZIRAH_FAULT_CANARY == 5,
               "fault trampoline codes out of sync with board.h");
