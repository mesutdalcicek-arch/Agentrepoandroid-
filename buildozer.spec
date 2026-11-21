[app]
title = Ajan
package.name = ajan
package.domain = org.mesut
source.dir = .
source.include_exts = py,html,xml
version = 0.1
requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,pyjnius,android,certifi
orientation = portrait
fullscreen = 0
android.permissions = INTERNET
android.api = 33
android.minapi = 24
android.archs = arm64-v8a
android.ndk = 25b
p4a.branch = v2024.01.21
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
