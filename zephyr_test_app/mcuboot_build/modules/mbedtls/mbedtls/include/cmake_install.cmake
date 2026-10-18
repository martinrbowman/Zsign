# Install script for directory: /Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include

# Set the install prefix
if(NOT DEFINED CMAKE_INSTALL_PREFIX)
  set(CMAKE_INSTALL_PREFIX "/usr/local")
endif()
string(REGEX REPLACE "/$" "" CMAKE_INSTALL_PREFIX "${CMAKE_INSTALL_PREFIX}")

# Set the install configuration name.
if(NOT DEFINED CMAKE_INSTALL_CONFIG_NAME)
  if(BUILD_TYPE)
    string(REGEX REPLACE "^[^A-Za-z0-9_]+" ""
           CMAKE_INSTALL_CONFIG_NAME "${BUILD_TYPE}")
  else()
    set(CMAKE_INSTALL_CONFIG_NAME "")
  endif()
  message(STATUS "Install configuration: \"${CMAKE_INSTALL_CONFIG_NAME}\"")
endif()

# Set the component getting installed.
if(NOT CMAKE_INSTALL_COMPONENT)
  if(COMPONENT)
    message(STATUS "Install component: \"${COMPONENT}\"")
    set(CMAKE_INSTALL_COMPONENT "${COMPONENT}")
  else()
    set(CMAKE_INSTALL_COMPONENT)
  endif()
endif()

# Is this installation the result of a crosscompile?
if(NOT DEFINED CMAKE_CROSSCOMPILING)
  set(CMAKE_CROSSCOMPILING "TRUE")
endif()

# Set path to fallback-tool for dependency-resolution.
if(NOT DEFINED CMAKE_OBJDUMP)
  set(CMAKE_OBJDUMP "/Users/martinbowman/.zephyr_ide/toolchains/zephyr-sdk-1.0.1/gnu/arm-zephyr-eabi/bin/arm-zephyr-eabi-objdump")
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/include/mbedtls" TYPE FILE PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ FILES
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/build_info.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/debug.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/error.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/mbedtls_config.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/net_sockets.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/oid.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/pkcs7.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/ssl.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/ssl_cache.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/ssl_ciphersuites.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/ssl_cookie.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/ssl_ticket.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/timing.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/version.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/x509.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/x509_crl.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/x509_crt.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/x509_csr.h"
    )
endif()

if(CMAKE_INSTALL_COMPONENT STREQUAL "Unspecified" OR NOT CMAKE_INSTALL_COMPONENT)
  file(INSTALL DESTINATION "${CMAKE_INSTALL_PREFIX}/include/mbedtls/private" TYPE FILE PERMISSIONS OWNER_READ OWNER_WRITE GROUP_READ WORLD_READ FILES
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/private/config_adjust_ssl.h"
    "/Volumes/embedded/zephyr_install/external/modules/crypto/mbedtls/include/mbedtls/private/config_adjust_x509.h"
    )
endif()

string(REPLACE ";" "\n" CMAKE_INSTALL_MANIFEST_CONTENT
       "${CMAKE_INSTALL_MANIFEST_FILES}")
if(CMAKE_INSTALL_LOCAL_ONLY)
  file(WRITE "/Volumes/embedded/Zsign/zephyr_test_app/mcuboot_build/modules/mbedtls/mbedtls/include/install_local_manifest.txt"
     "${CMAKE_INSTALL_MANIFEST_CONTENT}")
endif()
