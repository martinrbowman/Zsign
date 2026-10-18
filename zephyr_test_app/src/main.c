/*
 * Minimal MCUboot-bootable blinky, used as the Zsign Phase 4 DFU demo
 * target: the agent signs a build of this app, it gets flashed into
 * MCUboot's primary slot, and the blink period is the one observable
 * signal that the "new" signed image is actually running.
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>

#ifdef APP_VERSION_STRING
#include <app_version.h>
#endif

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);

int main(void)
{
#ifdef APP_VERSION_STRING
	printk("zsign blinky boot: version=%s blink_period_ms=%d\n",
	       APP_VERSION_STRING, CONFIG_APP_BLINK_PERIOD_MS);
#else
	printk("zsign blinky boot: blink_period_ms=%d\n", CONFIG_APP_BLINK_PERIOD_MS);
#endif

	if (!gpio_is_ready_dt(&led)) {
		printk("LED device not ready\n");
		return 0;
	}
	gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);

	while (1) {
		gpio_pin_toggle_dt(&led);
		k_msleep(CONFIG_APP_BLINK_PERIOD_MS);
	}
	return 0;
}
