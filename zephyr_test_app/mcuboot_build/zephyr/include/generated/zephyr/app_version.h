#ifndef _APP_VERSION_H_
#define _APP_VERSION_H_

/* The template values come from cmake/modules/version.cmake
 * BUILD_VERSION related template values will be 'git describe',
 * alternatively user defined BUILD_VERSION.
 */

/* #undef ZEPHYR_VERSION_CODE */
/* #undef ZEPHYR_VERSION */

#define APPVERSION                   0x2040000
#define APP_VERSION_NUMBER           0x20400
#define APP_VERSION_MAJOR            2
#define APP_VERSION_MINOR            4
#define APP_PATCHLEVEL               0
#define APP_TWEAK                    0
#define APP_EXTRAVERSION             rc1
#define APP_VERSION_STRING           "2.4.0-rc1"
#define APP_VERSION_EXTENDED_STRING  "2.4.0-rc1+0"
#define APP_VERSION_TWEAK_STRING     "2.4.0+0"

#define APP_BUILD_VERSION v2.4.0-rc1-3-gee39e2d694bd


#endif /* _APP_VERSION_H_ */
