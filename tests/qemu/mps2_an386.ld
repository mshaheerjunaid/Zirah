/* Memory map of QEMU's mps2-an386 (Cortex-M4F) for the Zirah emulator tests.
 * 0x00000000 is SSRAM1 (4 MB, writable in QEMU), so code and data both live
 * there and QEMU's ELF loader puts every section in place; nothing needs to
 * be copied at start-up. The stack sits at the top of SSRAM2/3. */
MEMORY
{
    CODE (rwx) : ORIGIN = 0x00000000, LENGTH = 4M
    RAM  (rwx) : ORIGIN = 0x20000000, LENGTH = 4M
}

ENTRY(Reset_Handler)

SECTIONS
{
    .text :
    {
        KEEP(*(.isr_vector))
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
    } > CODE

    .ARM.exidx : { *(.ARM.exidx*) } > CODE

    .data :
    {
        *(.data*)
        . = ALIGN(4);
    } > CODE

    .bss (NOLOAD) :
    {
        __bss_start__ = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(8);
        __bss_end__ = .;
    } > CODE

    end = .;
    __end__ = .;
    __HeapLimit = ORIGIN(CODE) + LENGTH(CODE);
    __StackTop = ORIGIN(RAM) + LENGTH(RAM);
    __stack = __StackTop;
}
