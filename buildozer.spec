[app]
title = Haofan
package.name = haofan
package.domain = org.haofan
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 0.1

requirements = python3,kivy
orientation = landscape
osx.python_version = 3
osx.kivy_version = 1.9.1

[buildozer]
log_level = 2

[app:android]
android.api = 33
android.sdk = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.skip_update = False
