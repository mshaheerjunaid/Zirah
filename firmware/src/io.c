#include "io.h"
#include "board.h"
#include "zirah_config.h"
#include "board_pins.h"

#include <string.h>

#include <libopencm3/stm32/rcc.h>
#include <libopencm3/stm32/gpio.h>
#include <libopencm3/stm32/usart.h>
#include <libopencm3/cm3/nvic.h>
#include <libopencm3/usb/usbd.h>
#include <libopencm3/usb/cdc.h>
#if defined(ZIRAH_BOARD_DEVEBOX)
#include <libopencm3/usb/dwc/otg_fs.h>
#endif

/* ------------------------------------------------------------------------ */
/* Receive rings: one per transport, each written by exactly one ISR.        */
/* ------------------------------------------------------------------------ */

#define RX_SIZE 1024u /* power of two */

typedef struct {
    volatile uint8_t buf[RX_SIZE];
    volatile uint16_t head; /* written by ISR  */
    volatile uint16_t tail; /* written by main */
} ring_t;

static ring_t rx_uart;
static ring_t rx_usb;

static void ring_put(ring_t *r, uint8_t b)
{
    uint16_t next = (uint16_t)((r->head + 1u) & (RX_SIZE - 1u));
    if (next != r->tail) {
        r->buf[r->head] = b;
        r->head = next;
    }
}

static int ring_get(ring_t *r)
{
    if (r->tail == r->head) {
        return -1;
    }
    uint8_t b = r->buf[r->tail];
    r->tail = (uint16_t)((r->tail + 1u) & (RX_SIZE - 1u));
    return b;
}

enum { SRC_NONE = 0, SRC_UART = 1, SRC_USB = 2 };
static int g_active = SRC_NONE;

/* ------------------------------------------------------------------------ */
/* The USB-TTL adapter's USART: USART2 on PA2/PA3 (Discovery) or USART1 on   */
/* PA9/PA10 (DevEBox, the same pins its ROM bootloader listens on)          */
/* ------------------------------------------------------------------------ */

#if ZIRAH_ENABLE_UART
void UART_ISR(void)
{
    if (usart_get_flag(UART_DEV, USART_SR_RXNE)) {
        ring_put(&rx_uart, (uint8_t)usart_recv(UART_DEV));
    }
    /* Clear overrun if it happened (read SR then DR). */
    if (USART_SR(UART_DEV) & USART_SR_ORE) {
        (void)USART_DR(UART_DEV);
    }
}

static void uart_init(void)
{
    rcc_periph_clock_enable(UART_RCC);
    gpio_mode_setup(UART_PORT, GPIO_MODE_AF, GPIO_PUPD_PULLUP, UART_PINS);
    gpio_set_af(UART_PORT, GPIO_AF7, UART_PINS);

    usart_set_baudrate(UART_DEV, ZIRAH_UART_BAUD);
    usart_set_databits(UART_DEV, 8);
    usart_set_stopbits(UART_DEV, USART_STOPBITS_1);
    usart_set_parity(UART_DEV, USART_PARITY_NONE);
    usart_set_flow_control(UART_DEV, USART_FLOWCONTROL_NONE);
    usart_set_mode(UART_DEV, USART_MODE_TX_RX);
    usart_enable_rx_interrupt(UART_DEV);
    usart_enable(UART_DEV);
    nvic_set_priority(UART_IRQ, 0x40);
    nvic_enable_irq(UART_IRQ);
}

static void uart_send(const uint8_t *p, size_t n)
{
    while (n--) {
        usart_send_blocking(UART_DEV, *p++);
    }
}
#else
static void uart_init(void) {}
static void uart_send(const uint8_t *p, size_t n) { (void)p; (void)n; }
#endif

/* ------------------------------------------------------------------------ */
/* USB CDC-ACM on the micro-USB OTG FS connector                             */
/* (descriptors follow the libopencm3 stm32f4-discovery usb_cdcacm example)  */
/* ------------------------------------------------------------------------ */

static usbd_device *g_usbd;
static volatile bool g_usb_configured;
static volatile bool g_usb_dtr;
static uint8_t usbd_control_buffer[128];

static struct usb_cdc_line_coding g_line_coding = {
    .dwDTERate = 115200,
    .bCharFormat = USB_CDC_1_STOP_BITS,
    .bParityType = USB_CDC_NO_PARITY,
    .bDataBits = 8,
};

static const struct usb_device_descriptor dev_desc = {
    .bLength = USB_DT_DEVICE_SIZE,
    .bDescriptorType = USB_DT_DEVICE,
    .bcdUSB = 0x0200,
    .bDeviceClass = USB_CLASS_CDC,
    .bDeviceSubClass = 0,
    .bDeviceProtocol = 0,
    .bMaxPacketSize0 = 64,
    .idVendor = 0x0483,  /* STMicroelectronics */
    .idProduct = 0x5740, /* Virtual COM Port   */
    .bcdDevice = 0x0200,
    .iManufacturer = 1,
    .iProduct = 2,
    .iSerialNumber = 3,
    .bNumConfigurations = 1,
};

static const struct usb_endpoint_descriptor comm_endp[] = {{
    .bLength = USB_DT_ENDPOINT_SIZE,
    .bDescriptorType = USB_DT_ENDPOINT,
    .bEndpointAddress = 0x83,
    .bmAttributes = USB_ENDPOINT_ATTR_INTERRUPT,
    .wMaxPacketSize = 16,
    .bInterval = 255,
}};

static const struct usb_endpoint_descriptor data_endp[] = {{
    .bLength = USB_DT_ENDPOINT_SIZE,
    .bDescriptorType = USB_DT_ENDPOINT,
    .bEndpointAddress = 0x01,
    .bmAttributes = USB_ENDPOINT_ATTR_BULK,
    .wMaxPacketSize = 64,
    .bInterval = 1,
}, {
    .bLength = USB_DT_ENDPOINT_SIZE,
    .bDescriptorType = USB_DT_ENDPOINT,
    .bEndpointAddress = 0x82,
    .bmAttributes = USB_ENDPOINT_ATTR_BULK,
    .wMaxPacketSize = 64,
    .bInterval = 1,
}};

static const struct {
    struct usb_cdc_header_descriptor header;
    struct usb_cdc_call_management_descriptor call_mgmt;
    struct usb_cdc_acm_descriptor acm;
    struct usb_cdc_union_descriptor cdc_union;
} __attribute__((packed)) cdcacm_functional_descriptors = {
    .header = {
        .bFunctionLength = sizeof(struct usb_cdc_header_descriptor),
        .bDescriptorType = CS_INTERFACE,
        .bDescriptorSubtype = USB_CDC_TYPE_HEADER,
        .bcdCDC = 0x0110,
    },
    .call_mgmt = {
        .bFunctionLength = sizeof(struct usb_cdc_call_management_descriptor),
        .bDescriptorType = CS_INTERFACE,
        .bDescriptorSubtype = USB_CDC_TYPE_CALL_MANAGEMENT,
        .bmCapabilities = 0,
        .bDataInterface = 1,
    },
    .acm = {
        .bFunctionLength = sizeof(struct usb_cdc_acm_descriptor),
        .bDescriptorType = CS_INTERFACE,
        .bDescriptorSubtype = USB_CDC_TYPE_ACM,
        .bmCapabilities = 0x02, /* supports SET/GET_LINE_CODING */
    },
    .cdc_union = {
        .bFunctionLength = sizeof(struct usb_cdc_union_descriptor),
        .bDescriptorType = CS_INTERFACE,
        .bDescriptorSubtype = USB_CDC_TYPE_UNION,
        .bControlInterface = 0,
        .bSubordinateInterface0 = 1,
    },
};

static const struct usb_interface_descriptor comm_iface[] = {{
    .bLength = USB_DT_INTERFACE_SIZE,
    .bDescriptorType = USB_DT_INTERFACE,
    .bInterfaceNumber = 0,
    .bAlternateSetting = 0,
    .bNumEndpoints = 1,
    .bInterfaceClass = USB_CLASS_CDC,
    .bInterfaceSubClass = USB_CDC_SUBCLASS_ACM,
    .bInterfaceProtocol = USB_CDC_PROTOCOL_AT,
    .iInterface = 0,
    .endpoint = comm_endp,
    .extra = &cdcacm_functional_descriptors,
    .extralen = sizeof(cdcacm_functional_descriptors),
}};

static const struct usb_interface_descriptor data_iface[] = {{
    .bLength = USB_DT_INTERFACE_SIZE,
    .bDescriptorType = USB_DT_INTERFACE,
    .bInterfaceNumber = 1,
    .bAlternateSetting = 0,
    .bNumEndpoints = 2,
    .bInterfaceClass = USB_CLASS_DATA,
    .bInterfaceSubClass = 0,
    .bInterfaceProtocol = 0,
    .iInterface = 0,
    .endpoint = data_endp,
}};

static const struct usb_interface ifaces[] = {{
    .num_altsetting = 1,
    .altsetting = comm_iface,
}, {
    .num_altsetting = 1,
    .altsetting = data_iface,
}};

static const struct usb_config_descriptor config_desc = {
    .bLength = USB_DT_CONFIGURATION_SIZE,
    .bDescriptorType = USB_DT_CONFIGURATION,
    .wTotalLength = 0,
    .bNumInterfaces = 2,
    .bConfigurationValue = 1,
    .iConfiguration = 0,
    .bmAttributes = 0x80,
    .bMaxPower = 0x32,
    .interface = ifaces,
};

/* Serial number = the chip's 96-bit unique ID, so every board shows up as a
 * distinct USB device and the host can tell two Zirah boards apart. */
static char g_usb_serial[25];
static const char *usb_strings[] = {
    "Zirah project",
    "Zirah PQC signer",
    g_usb_serial,
};

static enum usbd_request_return_codes
cdcacm_control_request(usbd_device *usbd_dev, struct usb_setup_data *req,
                       uint8_t **buf, uint16_t *len,
                       usbd_control_complete_callback *complete)
{
    (void)usbd_dev;
    (void)complete;

    switch (req->bRequest) {
    case USB_CDC_REQ_SET_CONTROL_LINE_STATE:
        g_usb_dtr = (req->wValue & 1u) != 0;
        return USBD_REQ_HANDLED;
    case USB_CDC_REQ_SET_LINE_CODING:
        if (*len < sizeof(struct usb_cdc_line_coding)) {
            return USBD_REQ_NOTSUPP;
        }
        memcpy(&g_line_coding, *buf, sizeof(struct usb_cdc_line_coding));
        return USBD_REQ_HANDLED;
    case 0x21: /* GET_LINE_CODING */
        *buf = (uint8_t *)&g_line_coding;
        *len = sizeof(struct usb_cdc_line_coding);
        return USBD_REQ_HANDLED;
    default:
        return USBD_REQ_NOTSUPP;
    }
}

static void cdcacm_data_rx_cb(usbd_device *usbd_dev, uint8_t ep)
{
    (void)ep;
    uint8_t tmp[64];
    int n = usbd_ep_read_packet(usbd_dev, 0x01, tmp, sizeof(tmp));
    for (int i = 0; i < n; i++) {
        ring_put(&rx_usb, tmp[i]);
    }
}

static void cdcacm_set_config(usbd_device *usbd_dev, uint16_t wValue)
{
    (void)wValue;
    usbd_ep_setup(usbd_dev, 0x01, USB_ENDPOINT_ATTR_BULK, 64, cdcacm_data_rx_cb);
    usbd_ep_setup(usbd_dev, 0x82, USB_ENDPOINT_ATTR_BULK, 64, NULL);
    usbd_ep_setup(usbd_dev, 0x83, USB_ENDPOINT_ATTR_INTERRUPT, 16, NULL);
    usbd_register_control_callback(usbd_dev,
                                   USB_REQ_TYPE_CLASS | USB_REQ_TYPE_INTERFACE,
                                   USB_REQ_TYPE_TYPE | USB_REQ_TYPE_RECIPIENT,
                                   cdcacm_control_request);
    g_usb_configured = true;
}

static void cdcacm_reset(void)
{
    g_usb_configured = false;
    g_usb_dtr = false;
}

void otg_fs_isr(void)
{
    usbd_poll(g_usbd);
}

static void usb_init(void)
{
    static const char hx[] = "0123456789ABCDEF";
    uint8_t uid[12];
    board_uid(uid);
    for (int i = 0; i < 12; i++) {
        g_usb_serial[2 * i] = hx[uid[i] >> 4];
        g_usb_serial[2 * i + 1] = hx[uid[i] & 15];
    }
    g_usb_serial[24] = 0;

    rcc_periph_clock_enable(RCC_OTGFS);
#if defined(ZIRAH_BOARD_DEVEBOX)
    /* PA9 is the serial adapter's pin here and the USB-C socket's VBUS is not
     * wired to it, so the USB core runs without VBUS sensing (below). */
    gpio_mode_setup(GPIOA, GPIO_MODE_AF, GPIO_PUPD_NONE, GPIO11 | GPIO12);
    gpio_set_af(GPIOA, GPIO_AF10, GPIO11 | GPIO12);
#else
    gpio_mode_setup(GPIOA, GPIO_MODE_AF, GPIO_PUPD_NONE, GPIO9 | GPIO11 | GPIO12);
    gpio_set_af(GPIOA, GPIO_AF10, GPIO9 | GPIO11 | GPIO12);
#endif

    g_usbd = usbd_init(&otgfs_usb_driver, &dev_desc, &config_desc,
                       usb_strings, 3,
                       usbd_control_buffer, sizeof(usbd_control_buffer));
#if defined(ZIRAH_BOARD_DEVEBOX)
    OTG_FS_GCCFG = (OTG_FS_GCCFG & ~(OTG_GCCFG_VBUSBSEN | OTG_GCCFG_VBUSASEN)) | OTG_GCCFG_NOVBUSSENS;
#endif
    usbd_register_set_config_callback(g_usbd, cdcacm_set_config);
    usbd_register_reset_callback(g_usbd, cdcacm_reset);

    nvic_set_priority(NVIC_OTG_FS_IRQ, 0x40);
    nvic_enable_irq(NVIC_OTG_FS_IRQ);
}

bool io_usb_configured(void)
{
    return g_usb_configured;
}

static void usb_send(const uint8_t *p, size_t n)
{
    while (n > 0) {
        uint16_t chunk = (uint16_t)(n > 64 ? 64 : n);
        uint32_t t0 = millis();
        for (;;) {
            if (!g_usb_configured) {
                return;
            }
            nvic_disable_irq(NVIC_OTG_FS_IRQ);
            uint16_t w = usbd_ep_write_packet(g_usbd, 0x82, p, chunk);
            nvic_enable_irq(NVIC_OTG_FS_IRQ);
            if (w) {
                break;
            }
            if ((uint32_t)(millis() - t0) > 200u) {
                return; /* host is not reading: drop rather than hang */
            }
        }
        p += chunk;
        n -= chunk;
    }
}

/* ------------------------------------------------------------------------ */
/* Output buffering                                                          */
/* ------------------------------------------------------------------------ */

static uint8_t g_out[256];
static size_t g_out_len;

static void send_to(int dst, const uint8_t *p, size_t n)
{
    if (dst == SRC_UART) {
        uart_send(p, n);
    } else if (dst == SRC_USB) {
        usb_send(p, n);
    } else {
        uart_send(p, n);
        if (g_usb_dtr) {
            usb_send(p, n);
        }
    }
}

void io_flush(void)
{
    if (g_out_len) {
        send_to(g_active, g_out, g_out_len);
        g_out_len = 0;
    }
}

void io_putc(char c)
{
    g_out[g_out_len++] = (uint8_t)c;
    if (g_out_len == sizeof(g_out) || c == '\n') {
        io_flush();
    }
}

void io_write(const uint8_t *buf, size_t len)
{
    while (len--) {
        g_out[g_out_len++] = *buf++;
        if (g_out_len == sizeof(g_out)) {
            io_flush();
        }
    }
}

void io_puts(const char *s)
{
    while (*s) {
        io_putc(*s++);
    }
}

void io_line(const char *s)
{
    io_puts(s);
    io_putc('\n');
}

void io_hex(const uint8_t *buf, size_t len)
{
    static const char hx[] = "0123456789abcdef";
    for (size_t i = 0; i < len; i++) {
        io_putc(hx[buf[i] >> 4]);
        io_putc(hx[buf[i] & 15]);
    }
}

void io_u32(uint32_t v)
{
    char tmp[11];
    int i = 0;
    do {
        tmp[i++] = (char)('0' + v % 10u);
        v /= 10u;
    } while (v);
    while (i) {
        io_putc(tmp[--i]);
    }
}

void io_i32(int32_t v)
{
    if (v < 0) {
        io_putc('-');
        io_u32((uint32_t)(-(int64_t)v));
    } else {
        io_u32((uint32_t)v);
    }
}

void io_kv_hex(const char *key, const uint8_t *buf, size_t len)
{
    io_puts(key);
    io_putc(' ');
    io_hex(buf, len);
    io_putc('\n');
}

void io_broadcast(const char *s)
{
    int saved = g_active;
    io_flush();
    g_active = SRC_NONE;
    io_puts(s);
    io_flush();
    g_active = saved;
}

/* ------------------------------------------------------------------------ */
/* Line assembly                                                             */
/* ------------------------------------------------------------------------ */

#define LINE_MAX 200

typedef struct {
    char buf[LINE_MAX + 1];
    size_t len;
    bool overflow; /* too long, or held a byte outside printable ASCII */
} line_t;

static line_t line_uart, line_usb;
static char g_ready[LINE_MAX + 1];

static bool feed(line_t *l, ring_t *r)
{
    int b;
    while ((b = ring_get(r)) >= 0) {
        if (b == '\r' || b == '\n') {
            if (l->len == 0 && !l->overflow) {
                continue;
            }
            bool ok = !l->overflow;
            l->buf[l->len] = 0;
            memcpy(g_ready, l->buf, l->len + 1);
            l->len = 0;
            l->overflow = false;
            if (ok) {
                return true;
            }
            g_ready[0] = 0;
            return true; /* caller sees empty line -> ERR */
        }
        /* Commands are plain printable ASCII. Control bytes, NUL and 8-bit
         * values invalidate the whole line instead of being half-parsed. */
        if (b < 0x20 || b > 0x7E) {
            l->overflow = true;
        } else if (l->len < LINE_MAX) {
            l->buf[l->len++] = (char)b;
        } else {
            l->overflow = true;
        }
    }
    return false;
}

const char *io_poll_line(void)
{
    if (feed(&line_usb, &rx_usb)) {
        g_active = SRC_USB;
        return g_ready;
    }
#if ZIRAH_ENABLE_UART
    if (feed(&line_uart, &rx_uart)) {
        g_active = SRC_UART;
        return g_ready;
    }
#endif
    return NULL;
}

bool io_rx_pending(void)
{
    return rx_usb.head != rx_usb.tail || rx_uart.head != rx_uart.tail;
}

void io_init(void)
{
    uart_init();
    usb_init();
}
