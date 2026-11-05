/*
 * Start-up for the emulator tests on QEMU's mps2-an386 (Cortex-M4F).
 *
 * The reset handler turns on the FPU (the pqm4 NTT keeps constants in FPU
 * registers), clears .bss, opens newlib's semihosting streams so printf and
 * the exit status reach the host, and runs main(). Faults print their name
 * and exit with status 3, so a crash can never look like a pass.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

extern uint32_t __bss_start__, __bss_end__, __StackTop;
extern void initialise_monitor_handles(void);
extern int main(void);

#define SCB_CPACR (*(volatile uint32_t *)0xE000ED88u)
#define SCB_CFSR  (*(volatile uint32_t *)0xE000ED28u)

void Reset_Handler(void);

/* newlib's exit() calls these; there are no static constructors here. */
void _init(void);
void _fini(void);
void _init(void) {}
void _fini(void) {}

void Reset_Handler(void)
{
    SCB_CPACR |= 0xFu << 20; /* CP10 and CP11 full access */
    __asm volatile("dsb\n isb" ::: "memory");
    for (uint32_t *p = &__bss_start__; p < &__bss_end__; p++) {
        *p = 0;
    }
    initialise_monitor_handles();
    exit(main());
}

static void fault(const char *what)
{
    printf("FAULT %s cfsr=%08lx\n", what, (unsigned long)SCB_CFSR);
    exit(3);
}

static void hard_fault(void) { fault("hard"); }
static void mem_fault(void) { fault("memory"); }
static void bus_fault(void) { fault("bus"); }
static void usage_fault(void) { fault("usage"); }
static void other(void) { fault("unexpected exception"); }

__attribute__((section(".isr_vector"), used))
static void (*const vectors[16])(void) = {
    (void (*)(void))(&__StackTop),
    Reset_Handler,
    other,       /* NMI */
    hard_fault,
    mem_fault,
    bus_fault,
    usage_fault,
    0, 0, 0, 0,
    other,       /* SVC */
    other,       /* debug monitor */
    0,
    other,       /* PendSV */
    other,       /* SysTick */
};
